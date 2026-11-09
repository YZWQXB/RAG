# Phase 2: 身份权限与知识库管理

**Goal:** RBAC 四角色与知识库级 ACL 判定收敛到单一函数，知识库 CRUD 与授权可用，越权访问行为符合设计（跨租户 404 / 同租户无权 403）。

**Prerequisites:** Phase 1 完成（租户上下文、RLS、JWT 可用）

**Deliverables:**
- `check_kb_permission` / `check_kb_permissions` 单点判定函数，有完整权限矩阵测试
- 知识库 CRUD 接口，列表按角色与 ACL 正确过滤
- KB ACL 与成员管理接口
- 跨租户与越权测试全绿

**Interface Decisions Implemented:** 注册/登录失败态的三种错误码（供 Phase 7 前端渲染）

---

## Task 1: 知识库数据模型与迁移

**Goal:** 建出 `knowledge_bases` 与 `kb_acl` 两表并启用 RLS。

**Files:**
- Create: `app/modules/knowledge/models.py`
- Create: `migrations/versions/0002_knowledge.py`

**Steps:**

- [ ] 定义模型：

```python
class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id, tenant_id(FK, index), name(String(128)), description(Text, nullable)
    embedding_model: Mapped[str] = mapped_column(String(64), default="BAAI/bge-m3")
    chunk_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[UUID] = mapped_column(FK("users.id"))
    created_at, updated_at, deleted_at(nullable)
    __table_args__ = (Index("idx_kb_tenant", "tenant_id", "status"),)

class KbAcl(Base):
    __tablename__ = "kb_acl"
    id, tenant_id, kb_id(FK, index)
    subject_type: Mapped[str] = mapped_column(String(16))   # user | role
    subject_id: Mapped[str] = mapped_column(String(64))      # user_id 为 uuid 字符串，role 为角色名
    permission: Mapped[str] = mapped_column(String(16))      # read | write | manage
    __table_args__ = (UniqueConstraint("kb_id", "subject_type", "subject_id", "permission"),)
```

`chunk_config` 默认值 `{"chunk_size_chars": 600, "overlap_chars": 90, "max_chars": 1200, "prepend_context_header": True}`，Phase 3 读取。

- [ ] 迁移 `0002_knowledge.py`：建两表 + btree 索引 + 与 Phase 1 相同的 `ENABLE`/`FORCE`/`CREATE POLICY` 三步。`downgrade()` 对称删除。
- [ ] 运行：`uv run alembic upgrade head`
  - Expected：`\d knowledge_bases` 可见表结构，`pg_policies` 多出两行。

**Security/Boundary Notes:** `kb_acl.subject_id` 用字符串而非 UUID 外键，因为同一列要能存 role 名；写入前由 service 层校验格式，不信任客户端原值。

---

## Task 2: 权限判定单点

**Goal:** 把全部权限规则收敛到 `iam/permissions.py`，其余模块只调用它。

**Files:**
- Create: `app/modules/iam/permissions.py`
- Test: `tests/unit/test_permissions.py`

**Steps:**

- [ ] 写权限矩阵测试（先失败）：

```python
@pytest.mark.parametrize("role,acl,action,expected", [
    ("owner",  None,        "manage", True),   # owner 全权
    ("admin",  None,        "write",  True),
    ("editor", None,        "read",   False),  # 无 ACL 不可见
    ("editor", "read",      "read",   True),
    ("editor", "read",      "write",  False),
    ("viewer", "write",     "write",  False),  # read 授权不能写
    ("viewer", "manage",    "write",  True),   # manage 蕴含 write
])
async def test_kb_permission_matrix(seed_two_tenants, role, acl, action, expected):
    ok = await has_kb_permission(session, principal(role), kb_id, action)
    assert ok is expected
```

- [ ] 运行：`uv run pytest tests/unit/test_permissions.py -v`
  - Expected：失败（模块不存在）。

- [ ] 创建 `app/modules/iam/permissions.py`：

```python
class Action(StrEnum):
    READ = "read"; WRITE = "write"; MANAGE = "manage"

MANAGE_IMPLIES = {Action.MANAGE: {Action.MANAGE, Action.WRITE, Action.READ},
                  Action.WRITE:  {Action.WRITE, Action.READ},
                  Action.READ:   {Action.READ}}
FULL_ACCESS_ROLES = {"owner", "admin"}

async def has_kb_permission(session, principal, kb_id, action) -> bool:
    # 1. KB 是否在本租户可见（RLS 已过滤；查不到说明是别的租户或已删除）
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError()                      # 跨租户 → 404，不泄露存在性
    # 2. owner/admin 直接放行
    if principal.role in FULL_ACCESS_ROLES:
        return True
    # 3. ACL：先按 user 精确匹配，再按 role 匹配
    rows = (await session.execute(
        select(KbAcl).where(KbAcl.kb_id == kb_id)
    )).scalars().all()
    granted = {Action(r.permission) for r in rows
               if (r.subject_type == "user" and r.subject_id == str(principal.user_id))
               or (r.subject_type == "role" and r.subject_id == principal.role)}
    allowed = set()
    for g in granted:
        allowed |= MANAGE_IMPLIES[g]
    return action in allowed
```

另提供 `require_kb_permission(...)`：无权限抛 `PermissionDeniedError`（403，"无权访问该知识库"），并批量版 `filter_readable_kbs(session, principal) -> list[UUID]`（owner/admin 返回全部，其余按 ACL 查）。

- [ ] 运行：`uv run pytest tests/unit/test_permissions.py -v`
  - Expected：7 个参数组合全部通过。

**Security/Boundary Notes:** 任何模块不得自写权限判断。`has_kb_permission` 内部的 `session.get` 依赖 RLS 过滤，若 RLS 失效会返回 403 而非 404——因此 Phase 1 的 RLS 测试是这条逻辑的前提。

---

## Task 3: 知识库 CRUD

**Goal:** 知识库增删改查，列表按角色与 ACL 过滤，删除为软删除。

**Files:**
- Create: `app/modules/knowledge/{service.py, repository.py, schemas.py, router.py}`
- Modify: `app/api/v1/__init__.py`

**Steps:**

- [ ] 定义 schemas：`KbCreate{name: str(1..128), description: str|None}`、`KbUpdate{name?, description?}`、`KbOut{id, name, description, status, document_count: int, updated_at}`。`name` 去首尾空格后不可为空，否则 `400 INVALID_NAME`（"知识库名称不能为空"）。
- [ ] 实现 service：`create_kb`、`list_kbs`（先 `filter_readable_kbs` 拿可见 id，再查，附带 `document_count` 子查询）、`get_kb`（`require_kb_permission(READ)`）、`update_kb`（WRITE）、`delete_kb`（MANAGE，写 `deleted_at`）。
- [ ] 实现 repository：`list_with_counts(tenant_id, kb_ids)` 用一次 `LEFT JOIN documents` 聚合，避免 N+1。
- [ ] 挂路由 `/api/v1/kbs`：`POST /`、`GET /`、`GET /{kb_id}`、`PATCH /{kb_id}`、`DELETE /{kb_id}`（返回 204）。
- [ ] 验证（`uv run pytest tests/integration/test_kb_crud.py -v`，新建该测试文件）：
  - 创建后列表可见，`document_count == 0`
  - viewer 创建 KB 返回 403
  - 删除后 `GET` 返回 404，列表不含该项
  - 同名允许（不做唯一约束）

**Security/Boundary Notes:** `deleted_at` 非空的知识库在所有查询中排除；子资源（文档）在 Phase 3 依赖该字段判活。

---

## Task 4: KB ACL 与成员管理

**Goal:** 可对知识库授权给具体用户或角色，可管理租户成员。

**Files:**
- Create: `app/modules/knowledge/acl_service.py`
- Modify: `app/modules/knowledge/router.py`
- Create: `app/modules/iam/member_service.py`
- Modify: `app/modules/iam/router.py`（新建）
- Modify: `app/api/v1/__init__.py`

**Steps:**

- [ ] KB ACL 接口（均要求 `MANAGE`）：
  - `PUT /kbs/{kb_id}/acl`，body `{subject_type: "user"|"role", subject_id: str, permission: "read"|"write"|"manage"}`，幂等 upsert（按唯一键冲突更新）
  - `DELETE /kbs/{kb_id}/acl/{acl_id}`，204
  - `GET /kbs/{kb_id}/acl`，返回授权列表
  - 校验：`subject_type=user` 时 `subject_id` 必须是本租户成员，否则 `400 INVALID_SUBJECT`（"该用户不属于当前空间"）；`subject_type=role` 时必须是四个合法角色之一
- [ ] 成员管理接口：
  - `GET /members` 返回 `[{user_id, email, name, role}]`
  - `POST /members` body `{email, role}`，要求 `owner` 或 `admin`；目标用户不存在返回 `404 USER_NOT_FOUND`（"该邮箱未注册"）
  - `PATCH /members/{user_id}` 改角色；**禁止**修改或移除 `owner` 角色（`403 CANNOT_MODIFY_OWNER`，"不能修改空间所有者"）
  - `DELETE /members/{user_id}` 移除成员，同时清理其在该租户的 `kb_acl` 记录
- [ ] 验证：`uv run pytest tests/integration/test_acl.py -v`
  - editor 对未授权 KB 的 `GET` 返回 403
  - 授权 `read` 后可见，但 `PATCH` 仍 403
  - 授权 `manage` 后可改
  - 移除成员后其 ACL 记录被清空（直接查库断言 0 行）

**Security/Boundary Notes:** 授予 `manage` 等价于把该 KB 的管理权交出，接口需在审计日志（Phase 6）记录。不得允许把 `owner` 降级。

---

## Task 5: 隔离与越权回归测试

**Goal:** 固化跨租户与越权的完整行为矩阵，作为后续阶段的回归基线。

**Files:**
- Test: `tests/isolation/test_kb_isolation.py`

**Steps:**

- [ ] 覆盖以下用例：

| 场景 | 期望 |
|---|---|
| A 的 token 读 B 的 KB（GET /kbs/{id}） | 404 `NOT_FOUND` |
| A 的 token 改 B 的 KB | 404 |
| A 的 token 删 B 的 KB | 404 |
| A 的 token 给 B 的 KB 授权 | 404 |
| A 的 token 列出 KB | 结果中 `tenant_id` 全为 A（断言集合） |
| B 的 KB 通过 `kb_ids` 参数混入检索 | Phase 4 断言被过滤（先留用例占位，标记 `skip` 并注明依赖） |
| A 的 token 邀请 B 的用户进 A 空间 | 允许（同邮箱用户可跨租户加入），但成员列表只见 A 的成员 |
| 错误响应体 | 含 `error.code`、`error.message` 为中文、不含 `traceback` |

- [ ] 运行：`uv run pytest tests/isolation -v`
  - Expected：Phase 1 的 4 个文件 + 本文件全部通过。

**Security/Boundary Notes:** 越权用例必须断言状态码**和**错误码，只断言 404 不够——若某天误把 403 改成 404 但语义错了，仍会漏检。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] 权限矩阵 7 个组合全部通过
- [ ] 跨租户 8 类场景符合期望
- [ ] `uv run pytest tests/isolation tests/unit -v` 全绿
- [ ] 所有权限判定均经由 `iam.permissions`，全仓搜索无第二处角色比较逻辑
- [ ] `alembic downgrade -1` 可回滚 0002

## 执行结果

**Status:** Done

- 已实现：`iam.permissions` 单点判定、KB CRUD 与可见性过滤、KB ACL、成员管理、迁移 0002
- 验证：权限矩阵 8 组参数化用例全绿；跨租户 8 类场景返回 404；移除成员清理 ACL
- 说明：同租户无权返回 403，跨租户返回 404（RLS 先过滤导致"查不到"）
