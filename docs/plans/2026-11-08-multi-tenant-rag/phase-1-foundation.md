# Phase 1: 工程骨架与租户隔离

**Goal:** 建立可运行的 FastAPI 骨架，数据库与 RLS 真实生效，租户可注册登录，四类隔离测试全绿。

**Prerequisites:** 无（项目起点）

**Deliverables:**
- `uv sync` 可安装成功，`uv run uvicorn app.main:app` 可启动
- `docker compose up -d postgres redis minio` 三个组件健康
- `uv run alembic upgrade head` 建出初始 4 张表并启用 RLS
- `uv run pytest tests/isolation -v` 四类隔离测试全部通过

**Interface Decisions Implemented:** 无（本阶段不含界面）

---

## Task 1: 项目骨架与依赖锁定

**Goal:** 建立可安装、可启动的最小工程，配置与错误响应骨架就位。

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `app/core/config.py`
- Create: `app/core/errors.py`
- Create: `app/core/logging.py`

**Steps:**

- [ ] 创建 `pyproject.toml`，索引指向清华 TUNA，声明依赖。

```toml
[project]
name = "rag-saas"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115,<0.116",
  "uvicorn[standard]>=0.32,<0.33",
  "sqlalchemy[asyncio]>=2.0.36,<2.1",
  "asyncpg>=0.30,<0.31",
  "alembic>=1.14,<1.15",
  "pydantic>=2.9,<3",
  "pydantic-settings>=2.6,<3",
  "pgvector>=0.3.6,<0.4",
  "pyjwt>=2.9,<3",
  "argon2-cffi>=23.1,<24",
  "structlog>=24.4,<25",
  "redis>=5.2,<6",
  "arq>=0.26,<0.27",
  "httpx>=0.27,<0.28",
  "boto3>=1.35,<2",
  "pymupdf>=1.24,<2",
  "python-docx>=1.1,<2",
  "markdown-it-py>=3.0,<4",
  "tiktoken>=0.8,<0.9",
  "python-multipart>=0.0.12,<0.1",
]

[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "pytest-cov>=6.0", "ruff>=0.8"]

[[tool.uv.index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

> pyproject 写兼容区间保证可维护性，**精确锁定由 `uv.lock` 提供且必须入库**。这是"版本精确锁定"的落地方式，不要手写 requirements.txt。

- [ ] 创建 `.gitignore`：`.venv/`、`__pycache__/`、`.env`、`.pytest_cache/`、`.ruff_cache/`、`data/`、`web/node_modules/`、`web/dist/`、`*.pyc`、`.coverage`。
- [ ] 创建 `.env.example`，列出全部配置项（真实值不入库）：

```dotenv
ENV=local
SECRET_KEY=change-me-in-production
DATABASE_URL=postgresql+asyncpg://rag_app:rag_app_pw@localhost:5432/rag
MIGRATION_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/rag
TEST_DATABASE_URL=postgresql+asyncpg://rag_app:rag_app_pw@localhost:5432/rag_test
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=rag-docs
S3_REGION=us-east-1
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
RERANKER_BASE_URL=https://api.siliconflow.cn/v1
RERANKER_API_KEY=sk-xxx
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=720
```

- [ ] 创建 `app/core/config.py`，用 `pydantic-settings` 读取，`Settings` 单例 `settings = Settings()`，字段名与 env 对应（小写映射）。`SECRET_KEY` 在 `ENV != "local"` 时禁止等于默认值，否则启动即抛错。
- [ ] 创建 `app/core/errors.py`：

```python
class AppError(Exception):
    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "服务异常"

class NotFoundError(AppError):
    code, status_code, message = "NOT_FOUND", 404, "资源不存在"

class QuotaExceededError(AppError):
    code, status_code, message = "QUOTA_EXCEEDED", 429, "本月额度已用尽"

class TenantContextMissing(AppError):
    code, status_code, message = "TENANT_CONTEXT_MISSING", 500, "租户上下文缺失"
```

并在 `app/main.py` 注册异常处理器，统一输出 `{"error": {"code", "message", "details"}}`，`message` 一律中文；未捕获异常统一转 `INTERNAL_ERROR` 且不返回堆栈。

- [ ] 创建 `app/core/logging.py`：structlog 输出 JSON，`processors` 含 `add_timestamp`、`add_trace_id`（从 contextvar 取）、脱敏 processor（正则替换 `sk-` 开头的 key、邮箱、手机号）。
- [ ] 创建 `app/main.py`：`create_app()` 装配路由前缀 `/api/v1`、中间件（Phase 6 补 trace_id）、`/healthz` 返回 `{"status": "ok"}`。
- [ ] 运行版本与安装：

```bash
uv python pin 3.12
uv sync
```

  - Expected：生成 `.python-version`、`uv.lock`、`.venv`；`uv sync` 无解析冲突。

- [ ] 运行启动自检：

```bash
uv run python -c "from app.main import create_app; print(create_app().title)"
```

  - Expected：打印 `RAG SaaS`（无异常）。

**Security/Boundary Notes:** `.env` 必须在 `.gitignore` 中；`SECRET_KEY` 默认值只允许在 `ENV=local` 下使用。依赖安装不走公网 pypi。

---

## Task 2: 基础设施编排与数据库角色

**Goal:** 一条命令起 PostgreSQL（含 pgvector/pg_trgm）、Redis、MinIO，并预置应用专用非超级用户角色。

**Files:**
- Create: `docker-compose.yml`
- Create: `docker/postgres-init.sql`

**Steps:**

- [ ] 创建 `docker/postgres-init.sql`。**这是 RLS 能生效的前提**：超级用户会绕过所有 RLS 策略，应用必须用非超级用户连接。

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE ROLE rag_app LOGIN PASSWORD 'rag_app_pw' NOSUPERUSER NOBYPASSRLS;
GRANT CONNECT ON DATABASE rag TO rag_app;
GRANT USAGE ON SCHEMA public TO rag_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rag_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO rag_app;

CREATE DATABASE rag_test OWNER postgres;
\connect rag_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
GRANT CONNECT ON DATABASE rag_test TO rag_app;
GRANT USAGE ON SCHEMA public TO rag_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rag_app;
```

- [ ] 创建 `docker-compose.yml`（本阶段只含基础设施，Phase 7 追加 api/worker）：

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: rag
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/postgres-init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d rag"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    ports: ["6379:6379"]
    volumes: [redisdata:/data]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio:
    image: minio/minio:RELEASE.2024-10-13T13-34-11Z
    command: ["server", "/data", "--console-address", ":9001"]
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]
    volumes: [miniodata:/data]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
  redisdata:
  miniodata:
```

- [ ] 启动并验证扩展与角色：

```bash
docker compose up -d postgres redis minio
docker compose exec -T postgres psql -U postgres -d rag -c "SELECT extname FROM pg_extension;"
docker compose exec -T postgres psql -U postgres -d rag -c "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='rag_app';"
```

  - Expected：`pg_extension` 含 `vector` 与 `pg_trgm`；`rag_app` 的 `rolsuper=f`、`rolbypassrls=f`。

- [ ] 验证应用角色可连接：

```bash
docker compose exec -T postgres psql -U rag_app -d rag -c "SELECT current_user;"
```

  - Expected：返回 `rag_app`，无权限错误。

**Security/Boundary Notes:** `rolbypassrls` 必须为 `false`，否则隔离测试会假绿。MinIO 默认凭据仅限本地，生产必须通过环境变量覆盖。

---

## Task 3: 数据库连接与租户上下文绑定

**Goal:** 提供 async engine/session 工厂，并实现基于 `SET LOCAL` 的租户绑定与仓储基类。

**Files:**
- Create: `app/core/db.py`
- Create: `app/core/tenant.py`
- Create: `app/core/repository.py`
- Test: `tests/unit/test_tenant_binding.py`

**Steps:**

- [ ] 写测试，验证绑定后 `current_setting` 可读、事务结束后失效。

```python
# tests/unit/test_tenant_binding.py
import uuid
from sqlalchemy import text
from app.core.db import session_scope
from app.core.tenant import bind_tenant, current_tenant_id

async def test_set_local_binds_within_transaction():
    tid = uuid.uuid4()
    async with session_scope() as s:
        await bind_tenant(s, tid)
        got = (await s.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar()
        assert got == str(tid)
        assert current_tenant_id() == tid
```

- [ ] 运行：`uv run pytest tests/unit/test_tenant_binding.py -v`
  - Expected：先失败（模块不存在）。

- [ ] 创建 `app/core/db.py`：

```python
engine = create_async_engine(
    settings.database_url, pool_size=10, max_overflow=5,
    pool_pre_ping=True, pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

@asynccontextmanager
async def session_scope(tenant_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        async with session.begin():
            if tenant_id is not None:
                await bind_tenant(session, tenant_id)
            yield session
```

同时提供 `admin_engine`（用 `MIGRATION_DATABASE_URL`，仅迁移与跨租户运维脚本使用），模块 docstring 写明"业务代码禁止使用"。

- [ ] 创建 `app/core/tenant.py`：

```python
_tenant_ctx: ContextVar[UUID | None] = ContextVar("tenant_id", default=None)

async def bind_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    # 必须是 SET LOCAL：SET 是会话级，连接归还池后会被下个租户复用，造成串数据
    await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_id)})
    _tenant_ctx.set(tenant_id)

def current_tenant_id() -> UUID:
    tid = _tenant_ctx.get()
    if tid is None:
        raise TenantContextMissing()
    return tid

def reset_tenant() -> None:
    _tenant_ctx.set(None)
```

- [ ] 创建 `app/core/repository.py`：`TenantScopedRepository` 基类，提供 `scope(stmt)` 方法自动追加 `Model.tenant_id == current_tenant_id()`，并统一 `get()`（查不到抛 `NotFoundError`，即跨租户访问返回 404）。
- [ ] 运行：`uv run pytest tests/unit/test_tenant_binding.py -v`
  - Expected：通过。

**Security/Boundary Notes:** 业务代码只允许通过 `session_scope(tenant_id=...)` 获取会话。任何直接 `SessionLocal()` 的使用都是评审阻断项。`SET LOCAL` 依赖活动事务，必须写在 `session.begin()` 内。

---

## Task 4: 初始迁移与 RLS 策略

**Goal:** 建出 `tenants`、`users`、`memberships`、`api_keys` 四表，并为租户表启用 FORCE RLS。

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_init.py`
- Create: `app/modules/tenancy/models.py`
- Create: `app/modules/iam/models.py`
- Test: `tests/isolation/test_db_rls.py`

**Steps:**

- [ ] 初始化 Alembic：`uv run alembic init -t async migrations`，改写 `migrations/env.py` 使用 `settings.migration_database_url`（超级用户）与 `Base.metadata`。
- [ ] 创建 ORM 模型：

```python
# app/modules/tenancy/models.py
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    plan: Mapped[str] = mapped_column(String(32), default="free")
    isolation_level: Mapped[str] = mapped_column(String(32), default="shared")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

# app/modules/iam/models.py
class User(Base):          # 全局表，无 tenant_id，不启用 RLS
    __tablename__ = "users"
    id, email(unique, index), password_hash, name, status, created_at

class Membership(Base):    # tenant_id + RLS
    __tablename__ = "memberships"
    id, tenant_id(FK tenants.id, index), user_id(FK users.id), role
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

class ApiKey(Base):        # tenant_id + RLS
    __tablename__ = "api_keys"
    id, tenant_id, name, key_hash, prefix, last_used_at, revoked_at, created_at
```

- [ ] 手写迁移 `0001_init.py`，建表后**逐表**执行（`tenants` 用 `id`，其余用 `tenant_id`）：

```python
for table, col in [("tenants", "id"), ("memberships", "tenant_id"), ("api_keys", "tenant_id")]:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({col} = current_setting('app.tenant_id', true)::uuid) "
        f"WITH CHECK ({col} = current_setting('app.tenant_id', true)::uuid)"
    )
```

`downgrade()` 必须对称：先 `DROP POLICY`、再 `DISABLE`、再 `DROP TABLE`。

- [ ] 应用迁移并验证策略已挂载：

```bash
uv run alembic upgrade head
docker compose exec -T postgres psql -U postgres -d rag -c "SELECT tablename, policyname FROM pg_policies;"
```

  - Expected：三行策略，表名为 `tenants`/`memberships`/`api_keys`。

- [ ] 写 RLS 隔离测试（**这是全项目最重要的一条测试**）：

```python
# tests/isolation/test_db_rls.py
async def test_rls_blocks_cross_tenant_read(seed_two_tenants):
    a, b, kb_a, kb_b = seed_two_tenants
    async with session_scope(tenant_id=a) as s:
        rows = (await s.execute(select(Membership.tenant_id))).scalars().all()
    assert set(rows) == {a}          # 看不到 b

async def test_rls_blocks_cross_tenant_write(seed_two_tenants):
    a, b, *_ = seed_two_tenants
    async with session_scope(tenant_id=a) as s:
        with pytest.raises(Exception):      # RLS WITH CHECK 拒绝
            await s.execute(insert(Membership).values(tenant_id=b, user_id=uuid4(), role="viewer"))
```

- [ ] 运行：`uv run pytest tests/isolation/test_db_rls.py -v`
  - Expected：两条通过。若第一条失败（读到两家数据），先检查应用连接是否误用超级用户。

**Security/Boundary Notes:** `FORCE ROW LEVEL SECURITY` 不可省略，否则表 owner 绕过策略。策略必须写 `WITH CHECK`，否则只能防读不能防写越权。

---

## Task 5: 注册登录与 API 级隔离测试

**Goal:** 注册即创建租户并成为 owner，登录返回内嵌 `tenant_id` 的 JWT，接口层跨租户返回 404。

**Files:**
- Create: `app/modules/tenancy/service.py`
- Create: `app/modules/iam/service.py`
- Create: `app/core/security.py`
- Create: `app/api/v1/auth.py`
- Create: `app/api/v1/deps.py`
- Create: `app/modules/tenancy/router.py`
- Create: `scripts/__init__.py`
- Create: `scripts/seed.py`
- Create: `tests/conftest.py`
- Test: `tests/isolation/test_api_isolation.py`
- Test: `tests/isolation/test_pool_reuse.py`

**Steps:**

- [ ] 创建 `app/core/security.py`：`hash_password`/`verify_password`（Argon2id）、`create_access_token(user_id, tenant_id, role)`、`decode_token(token) -> Principal`。JWT payload 固定为 `{"sub": str(user_id), "tid": str(tenant_id), "role": role, "exp": ...}`。
- [ ] 创建 `app/modules/tenancy/service.py::register_tenant(email, password, tenant_name, slug)`。**注意注册时的 RLS 悖论**：此时还没有租户上下文，但 `tenants` 表有 `WITH CHECK` 策略。解法是先生成 `tenant_id`，用它绑定会话，再插三张表：

```python
async def register_tenant(...) -> tuple[Tenant, User]:
    tenant_id = uuid4()
    async with SessionLocal() as session:
        async with session.begin():
            await bind_tenant(session, tenant_id)   # 先绑定，WITH CHECK 才通过
            tenant = Tenant(id=tenant_id, slug=slug, name=tenant_name)
            user = User(email=email, password_hash=hash_password(password), name=email.split("@")[0])
            session.add_all([tenant, user])
            await session.flush()
            session.add(Membership(tenant_id=tenant_id, user_id=user.id, role="owner"))
    return tenant, user
```

slug 冲突返回 `409 SLUG_TAKEN`，文案"该空间标识已被占用"；邮箱重复返回 `409 EMAIL_TAKEN`，文案"该邮箱已注册"。

- [ ] 创建 `app/modules/iam/service.py::authenticate(email, password)` 与 `list_memberships(user_id)`。登录接口：单租户直接返回 token；多租户返回 `{"tenants": [...], "selection_required": true}`，由客户端带 `tenant_id` 再调 `POST /auth/token` 换取 token。
- [ ] 创建 `app/api/v1/deps.py`：

```python
async def get_principal(cred = Depends(HTTPBearer())) -> Principal:
    return decode_token(cred.credentials)     # 含 user_id / tenant_id / role

async def get_session(p: Principal = Depends(get_principal)):
    async with session_scope(tenant_id=p.tenant_id) as s:
        yield s
```

  `Principal` 是 `dataclass(frozen=True)`，字段 `user_id`、`tenant_id`、`role`。
- [ ] 创建 `app/api/v1/auth.py`：`POST /auth/register`、`POST /auth/login`、`POST /auth/token`、`GET /auth/me`。响应字段用 `schemas` 定义，不直接返回 ORM。
- [ ] 新增 `GET /api/v1/tenants/{tenant_id}`（挂在 `app/modules/tenancy/router.py`）：`session.get(Tenant, tenant_id)`，查不到抛 `NotFoundError`。因为 RLS 只放行本租户行，访问其他租户 id 必然查不到 → 404。**这个接口是 Task 5 隔离测试的被测对象**，Phase 2 之后复用同一模式。
- [ ] 建 `tests/conftest.py`：`seed_two_tenants` fixture 用 `admin_engine` 直插两个租户 + 各一个用户 + 各一个 membership，返回 `(a, b, user_a, user_b)`；`client_a`/`client_b` fixture 生成带各自 token 的 `AsyncClient`。每个用例结束 `TRUNCATE` 全部业务表（`RESTART IDENTITY CASCADE`）。
- [ ] 创建 `scripts/__init__.py`（空）与 `scripts/seed.py`：可用 `uv run python -m scripts.seed` 在本地库造两个租户（`demo-a` / `demo-b`）与各一个 owner 账号，密码统一 `Passw0rd!`，用于手工验证隔离；重复执行幂等（按 slug upsert）。
- [ ] 写 API 隔离测试（断言 **404 而非 403**）：

```python
async def test_cannot_read_other_tenant_resource(client_a, b):
    r = await client_a.get(f"/api/v1/tenants/{b}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
```

- [ ] 写连接池复用测试（验证 `SET LOCAL` 未泄漏）：

```python
async def test_pool_reuse_no_tenant_leak(client_a, client_b, a, b):
    for _ in range(50):
        assert (await client_a.get("/api/v1/auth/me")).json()["tenant_id"] == str(a)
        assert (await client_b.get("/api/v1/auth/me")).json()["tenant_id"] == str(b)
```

- [ ] 运行全部隔离测试：`uv run pytest tests/isolation -v`
  - Expected：4 个文件全部通过。

**Security/Boundary Notes:** 不接受任何来自请求体或 query 的 `tenant_id`，一律以 JWT claim 为准。密码最少 8 位且包含字母与数字，不满足返回 `400 WEAK_PASSWORD`（中文提示）。登录失败统一返回"邮箱或密码错误"，不区分账号不存在与密码错误。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] `uv run pytest tests/isolation -v` 四类隔离测试全绿
- [ ] `uv run pytest tests/unit -v` 通过
- [ ] `uv run alembic downgrade base && uv run alembic upgrade head` 可往返
- [ ] `GET /healthz` 返回 200
- [ ] 无业务代码直接调用 `SessionLocal()` 或 `admin_engine`（除注册与测试 fixture）
- [ ] 所有错误响应 `message` 为中文

## 执行结果

**Status:** Done

- 已实现：配置/错误/日志层、async 引擎与会话、`set_config` 租户绑定、Alembic 0001 与 RLS、注册登录与 JWT、`GET /tenants/{id}`
- 验证：`pytest tests/isolation -v` 四类隔离全绿（接口 404 / RLS 读写拦截 / 未绑定 fail-closed / 连接池交替 100 次不泄漏）
- 偏差：`SET LOCAL` 改为 `set_config(..., is_local=true)`，因 PostgreSQL 的 `SET` 不接受绑定参数
- 偏差：RLS 策略加 `nullif(...,'')`，否则自定义 GUC 被重置为空串时 `::uuid` 会抛错而非返回 0 行
