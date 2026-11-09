# 多租户 RAG 知识库系统

对外 SaaS 形态的多租户 RAG 知识库：客户自助注册，在各自空间内上传文档、构建知识库、获得带页码引用的问答。

- 设计文档：`docs/specs/2026-11-08-multi-tenant-rag-design.md`
- 实施计划：`docs/plans/2026-11-08-multi-tenant-rag/`
- 自检与修复报告：`docs/reports/2026-11-09-self-check-and-fixes.md`

## 技术栈

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 16 + pgvector · Redis 7 · React 18 + Vite + Tailwind v4 · Docker Compose

## 快速开始

```bash
# 1. 准备配置（本地无 API Key 时默认使用假模型，可离线跑通全链路）
cp .env.example .env
#    生产/真实效果：把 EMBEDDING_PROVIDER / LLM_PROVIDER / RERANKER_PROVIDER 改为 api 并填 Key

# 2. 起基础设施
docker compose up -d postgres redis

# 3. 安装依赖（uv 会按 .python-version 自动使用 3.12）
uv sync

# 4. 执行迁移
uv run alembic upgrade head

# 5. 本地起服务（两个终端）
uv run uvicorn app.main:app --reload --port 8000
uv run python -m worker.main

# 6. 冒烟
uv run python -m scripts.smoke --base http://127.0.0.1:8000
```

打开 <http://localhost:5173> 使用前端（另开一个终端 `cd web && npm run dev`），或按下节用容器整体启动。

## 容器化一键启动

```bash
docker compose up -d --build
```

| 服务 | 地址 | 说明 |
|---|---|---|
| web | <http://localhost:8080> | nginx 静态站 + `/api` 反向代理（SSE 已关缓冲）|
| api | <http://localhost:8000> | FastAPI，启动时自动 `alembic upgrade head` |
| worker | — | outbox 轮询 + 定时维护 |
| postgres / redis | 5432 / 6379 | |
| minio | 9000 / 9001 | 仅在 `--profile s3` 时启动 |

冒烟：`uv run python -m scripts.smoke --base http://localhost:8080`

## 常用命令

```bash
uv run pytest -q                      # 全量测试
uv run pytest tests/isolation -v      # 四类隔离测试（CI 阻断项）
uv run ruff check app worker scripts  # 静态检查
uv run alembic downgrade -1           # 回滚一个迁移
uv run python -m scripts.seed         # 造两个演示租户（demo-a / demo-b）
uv run python -m scripts.build_eval_corpus && uv run python -m scripts.eval   # 检索质量回归
```

## 架构要点

**四道租户防线**：JWT 内嵌 `tenant_id` → 仓储层自动注入过滤 → PostgreSQL RLS 兜底 → 对象存储按租户前缀。

两个容易踩的坑，已在代码中固定：

1. `SET` 是会话级、`SET LOCAL` 是事务级。用 `SET` 会让连接池复用时串租户，因此统一用 `set_config(name, value, true)`（`SET` 不支持绑定参数）。
2. 事务提交后 `SET LOCAL` 失效。入库状态机每阶段提交后都重新绑定租户，否则后续 UPDATE 会被 RLS 过滤成 0 行。

**入库触发用 outbox 表**而非直接投 Redis：上传接口里"写库"和"推消息"无法原子完成，outbox 让二者同事务，牺牲 1 秒轮询延迟换取不丢任务；Redis 挂掉时入库仍可用。

**检索**：pgvector 余弦召回 + `pg_trgm` 三元组召回，RRF 融合，bge-reranker 重排（失败自动降级为不重排）。中文 BM25（zhparser）为后续优化项。

## 数据库角色

| 角色 | 用途 | 权限 |
|---|---|---|
| `postgres` | 仅迁移与运维脚本 | 超级用户，**绕过 RLS** |
| `rag_app` | 应用连接 | `NOSUPERUSER NOBYPASSRLS`，受 RLS 约束 |

应用若误用超级用户，隔离测试会假绿、上线后串数据——这是最需要守住的一条。

## 运维与安全

**健康检查**——两个端点语义不同，探针不要混用：

| 端点 | 判定 | 典型用途 |
|---|---|---|
| `GET /healthz` | 进程能响应即 200，**不查依赖** | liveness（容器存活）|
| `GET /readyz` | 实际执行 `SELECT 1` 并 ping Redis，任一失败返回 **503** | readiness（负载均衡摘流、启动门禁）|

把数据库抖动算进存活判定，会让一次缓存故障升级成编排系统批量重启实例——故障被放大而不是被隔离，因此刻意拆成两个端点。

**审计日志**：`GET /api/v1/audit-logs?action=&limit=`，仅 `owner` / `admin` 可读。已埋点 `kb.create`、`kb.update`、`kb.delete`、`kb.acl.upsert`、`kb.acl.delete`、`member.add`、`member.role`、`member.remove`、`document.delete`。日志与业务写在**同一个事务**里：业务回滚，日志一起消失，不会留下"有操作记录、没实际数据"的假轨迹；查询侧由 RLS 按租户隔离。

**登录限流**：按来源 IP 的 Redis 固定窗口计数（`LOGIN_RATE_LIMIT_PER_MINUTE`，默认 20 次/分钟），超限返回 `429` + `Retry-After`。账号密码连续错误另有临时锁定——两者互补：账号锁定防单账号爆破，IP 限流防换邮箱横向尝试。

**生产环境启动护栏**（不满足直接启动失败，避免带病上线）：

- `SECRET_KEY` 必须是显式配置，不能用示例默认值
- 禁止 `EMBEDDING_PROVIDER=fake`（假向量没有语义能力）
- 禁止 `STORAGE_BACKEND=local`（多副本时写文件与读文件可能不是同一台机器，入库会静默失败）
- 检索调试路由即使 `ENABLE_DEBUG_API=true` 也不挂载

**资源上限**：上传单文件 50 MB；`docx` 等 zip 容器按声明解压体积拦截，上限 200 MB（防解压炸弹）；单文档入库总超时 300 s，超时标记 `failed` 并写中文错误；SSE 每 15 s 发一次心跳帧，避免网关空闲断流。

## 已知限制

- 假模型 Provider（`*_PROVIDER=fake`）只做字符级相似度，**没有语义能力**，仅用于离线联调；真实效果必须配置 API Key。
- 本地存储后端不支持签名 URL（`presign_get` 抛 `NotImplementedError`），需要直链请切 `STORAGE_BACKEND=s3`。
- 前端 token 存 localStorage（XSS 面更大），后续应迁到 httpOnly cookie + CSRF。
- 多模态解析、外部数据源连接器、Agent、支付均不在本期范围。
