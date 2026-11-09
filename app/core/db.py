"""数据库引擎与会话。

两个引擎，职责严格区分：
- engine / SessionLocal：业务使用，连接用户 rag_app，受 RLS 约束。
- admin_engine / AdminSession：迁移、跨租户调度、运维脚本专用，连接超级用户，绕过 RLS。

业务代码只允许通过 session_scope() 获取会话。直接使用 SessionLocal 或 admin_engine
都是代码评审的阻断项（例外清单见各文件注释）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

# 仅用于迁移、worker 的跨租户调度、运维脚本与测试 fixture
admin_engine = create_async_engine(
    settings.migration_database_url,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    echo=False,
)

AdminSession = async_sessionmaker(admin_engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(tenant_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    """单事务会话。传入 tenant_id 时自动绑定 RLS 上下文。"""
    from app.core.tenant import bind_tenant

    async with SessionLocal() as session:
        async with session.begin():
            if tenant_id is not None:
                await bind_tenant(session, tenant_id)
            yield session


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """跨租户会话，业务代码禁用。"""
    async with AdminSession() as session:
        async with session.begin():
            yield session


async def dispose_engines() -> None:
    await engine.dispose()
    await admin_engine.dispose()
