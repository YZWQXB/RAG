from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.core.errors import TenantContextMissing
from app.core.tenant import bind_tenant, current_tenant_id

pytestmark = pytest.mark.db


async def test_set_local_binds_within_transaction(seed_two_tenants):
    tenant_id = seed_two_tenants[0]
    async with session_scope(tenant_id=tenant_id) as session:
        got = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar()
        assert got == str(tenant_id)
        assert current_tenant_id() == tenant_id


async def test_set_local_is_scoped_to_transaction(seed_two_tenants):
    tenant_id = seed_two_tenants[0]
    async with session_scope(tenant_id=tenant_id):
        pass
    # 事务结束后连接归还池中，SET LOCAL 的值必须失效
    async with session_scope() as session:
        got = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar()
        assert got in (None, "")


def test_current_tenant_missing_raises():
    from app.core.context import set_tenant_id

    set_tenant_id(None)
    with pytest.raises(TenantContextMissing):
        current_tenant_id()


async def test_bind_tenant_sets_context(seed_two_tenants):
    tenant_id = seed_two_tenants[1]
    async with session_scope() as session:
        await bind_tenant(session, tenant_id)
        assert current_tenant_id() == tenant_id
