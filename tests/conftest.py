"""测试夹具。

两层隔离：
1. 数据库隔离：进入测试前把 DATABASE_URL / MIGRATION_DATABASE_URL 指向 rag_test，
   避免测试污染开发库。必须在导入任何 app 模块之前完成，因此本文件顶部就设置环境变量。
2. 环境隔离：数据库不可用时，@pytest.mark.db 的用例被跳过并给出原因，不伪装通过。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import pytest

# ---- 必须最先执行：把应用指向测试库 ----
TEST_DB_URL = "postgresql+asyncpg://rag_app:rag_app_pw@localhost:5432/rag_test"
TEST_ADMIN_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_test"
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["MIGRATION_DATABASE_URL"] = TEST_ADMIN_URL
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_DIR"] = ".tmp-test-storage"
os.environ["ENABLE_DEBUG_API"] = "true"

# 必须在任何业务模块之前导入，保证所有 ORM 模型都注册到 Base.metadata，
# 否则单独跑某个模块的用例会报 NoReferencedTableError（外键目标表未注册）。
import app.core.registry  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent


def _postgres_reachable() -> bool:
    parsed = urlparse(TEST_DB_URL.replace("+asyncpg", ""))
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


DB_AVAILABLE = _postgres_reachable()


def pytest_configure(config):
    config.addinivalue_line("markers", "db: 需要可用的 PostgreSQL")


def pytest_collection_modifyitems(config, items):
    if DB_AVAILABLE:
        return
    reason = "需要可用的 PostgreSQL：docker compose up -d postgres，并保证 rag_test 库存在"
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
async def prepare_database():
    """对测试库执行一次迁移，顺带验证迁移脚本本身可用。

    迁移后必须补授权：schema 若被重建过，ALTER DEFAULT PRIVILEGES 会随 schema 一起消失，
    导致 rag_app 没有表权限。这里显式授权，保证夹具不依赖 init 脚本的一次性副作用。
    """
    if not DB_AVAILABLE:
        yield
        return
    env = dict(os.environ)
    env["MIGRATION_DATABASE_URL"] = TEST_ADMIN_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"测试库迁移失败:\n{result.stdout}\n{result.stderr}")

    from sqlalchemy import text

    from app.core.db import admin_engine

    async with admin_engine.begin() as conn:
        for stmt in (
            "GRANT USAGE ON SCHEMA public TO rag_app",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rag_app",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_app",
        ):
            await conn.execute(text(stmt))

    # 限流计数是跨用例共享的（同一个测试 IP），每轮必须清零，否则用例互相干扰
    try:
        from app.core.redis_client import get_redis

        redis = get_redis()
        buckets = [key async for key in redis.scan_iter("rl:*")]
        if buckets:
            await redis.delete(*buckets)
    except Exception:
        pass

    # 清掉向量缓存，避免上一轮（可能是不同 provider 实现）的向量污染本轮结果
    try:
        from app.core.redis_client import get_redis

        redis = get_redis()
        stale = [key async for key in redis.scan_iter("emb:*")]
        if stale:
            await redis.delete(*stale)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
async def clean_tables(prepare_database):
    # 限流计数按"分钟窗口 + 来源"共享，跨用例不清零会让后面用例莫名 429
    try:
        from app.core.redis_client import get_redis

        redis = get_redis()
        buckets = [key async for key in redis.scan_iter("rl:*")]
        if buckets:
            await redis.delete(*buckets)
    except Exception:
        pass

    yield
    if not DB_AVAILABLE:
        return
    from sqlalchemy import text

    from app.core.db import admin_engine

    async with admin_engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if rows:
            await conn.execute(text(f"TRUNCATE {', '.join(rows)} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def seed_two_tenants() -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """直插两个租户，用于隔离断言。"""
    from sqlalchemy import text

    from app.core.db import admin_session

    a, b = uuid.uuid4(), uuid.uuid4()
    async with admin_session() as session:
        for tenant_id, slug in ((a, "tenant-a"), (b, "tenant-b")):
            await session.execute(
                text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)"),
                {"id": tenant_id, "slug": slug, "name": slug},
            )
            user_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, name) "
                    "VALUES (:id, :email, :pw, :name)"
                ),
                {"id": user_id, "email": f"{slug}@example.com", "pw": "x", "name": slug},
            )
            await session.execute(
                text(
                    "INSERT INTO memberships (id, tenant_id, user_id, role) "
                    "VALUES (:id, :tid, :uid, 'owner')"
                ),
                {"id": uuid.uuid4(), "tid": tenant_id, "uid": user_id},
            )
    yield a, b


async def _seed_account(slug: str, email: str, role: str = "owner") -> tuple:
    from sqlalchemy import text

    from app.core.db import admin_session
    from app.core.security import create_access_token, hash_password

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with admin_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)"),
            {"id": tenant_id, "slug": slug, "name": slug},
        )
        await session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name) "
                "VALUES (:id, :email, :pw, :name)"
            ),
            {"id": user_id, "email": email, "pw": hash_password("Passw0rd!"), "name": slug},
        )
        await session.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, role) "
                "VALUES (:id, :tid, :uid, :role)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "uid": user_id, "role": role},
        )
    return tenant_id, user_id, create_access_token(user_id, tenant_id, role)


@pytest.fixture
async def tenant_a() -> tuple:
    return await _seed_account("tenant-a", "a@example.com")


@pytest.fixture
async def tenant_b() -> tuple:
    return await _seed_account("tenant-b", "b@example.com")


def _client_for(token: str | None):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=headers,
        timeout=60,
    )


@pytest.fixture
async def client_a(tenant_a):
    async with _client_for(tenant_a[2]) as client:
        yield client


@pytest.fixture
async def client_b(tenant_b):
    async with _client_for(tenant_b[2]) as client:
        yield client


@pytest.fixture
async def anon_client():
    async with _client_for(None) as client:
        yield client


async def create_member(tenant_id: uuid.UUID, role: str, email: str) -> tuple:
    """在指定租户下建一个指定角色的成员，返回 (user_id, token)。"""
    from sqlalchemy import text

    from app.core.db import admin_session
    from app.core.security import create_access_token, hash_password

    user_id = uuid.uuid4()
    async with admin_session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name) "
                "VALUES (:id, :email, :pw, :name)"
            ),
            {"id": user_id, "email": email, "pw": hash_password("Passw0rd!"), "name": role},
        )
        await session.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, role) "
                "VALUES (:id, :tid, :uid, :role)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "uid": user_id, "role": role},
        )
    return user_id, create_access_token(user_id, tenant_id, role)


@pytest.fixture(autouse=True)
def fake_providers():
    """默认注入确定性替身，避免测试依赖网络与真实 API Key。

    需要特定行为的用例（如模拟生成中断）可在用例内部再次 set_xxx_provider 覆盖。
    """
    from app.providers import (
        reset_providers,
        set_embedding_provider,
        set_llm_provider,
        set_reranker_provider,
    )
    from app.providers.embedding.fake import FakeEmbedding
    from app.providers.llm.fake import FakeLLM
    from app.providers.reranker.fake import FakeReranker

    set_embedding_provider(FakeEmbedding())
    set_llm_provider(FakeLLM())
    set_reranker_provider(FakeReranker())
    yield
    reset_providers()


@pytest.fixture
def low_threshold(monkeypatch):
    """降低无答案阈值。

    测试用的假向量只有字符级相似度，达不到真实 embedding 的语义水平，
    因此"想验证生成链路"的用例显式放宽阈值；阈值本身由专门用例验证。
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.05)


@pytest.fixture
async def viewer_client_a(tenant_a):
    """租户 A 中的一个 viewer 客户端。"""
    _, token = await create_member(tenant_a[0], "viewer", "viewer-a@example.com")
    async with _client_for(token) as client:
        yield client


@pytest.fixture
async def editor_client_a(tenant_a):
    _, token = await create_member(tenant_a[0], "editor", "editor-a@example.com")
    async with _client_for(token) as client:
        yield client
