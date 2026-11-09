"""数据库层隔离测试——全项目最重要的一组用例。

若这两条失败，说明 RLS 没有真正生效（常见原因：应用连接误用超级用户）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import admin_session, session_scope
from app.modules.iam.models import Membership

pytestmark = pytest.mark.db


async def test_rls_blocks_cross_tenant_read(seed_two_tenants):
    tenant_a, tenant_b = seed_two_tenants
    async with session_scope(tenant_id=tenant_a) as session:
        rows = (await session.execute(select(Membership.tenant_id))).scalars().all()
    assert set(rows) == {tenant_a}, "租户 A 不应看到租户 B 的成员记录"


async def test_rls_blocks_cross_tenant_write(seed_two_tenants):
    tenant_a, tenant_b = seed_two_tenants
    with pytest.raises(Exception) as excinfo:
        async with session_scope(tenant_id=tenant_a) as session:
            await session.execute(
                text(
                    "INSERT INTO memberships (id, tenant_id, user_id, role) "
                    "VALUES (:id, :tid, :uid, 'viewer')"
                ),
                {"id": uuid.uuid4(), "tid": tenant_b, "uid": uuid.uuid4()},
            )
    assert "row-level security" in str(excinfo.value).lower()


async def test_rls_fails_closed_without_tenant(seed_two_tenants):
    """未绑定租户时必须读不到任何行，而不是读到全部。"""
    async with session_scope() as session:
        rows = (await session.execute(select(Membership.id))).scalars().all()
    assert rows == []


async def test_admin_session_can_see_all_tenants(seed_two_tenants):
    """admin_engine 是跨租户运维路径，必须能看到全部，否则 worker 无法调度。"""
    async with admin_session() as session:
        rows = (await session.execute(select(Membership.tenant_id))).scalars().all()
    assert set(rows) == set(seed_two_tenants)
