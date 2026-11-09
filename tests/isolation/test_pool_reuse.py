"""连接池复用测试：验证 SET LOCAL 没有把租户上下文泄漏给下一个请求。

这是最容易出问题的地方——如果实现里用了 SET（会话级）而不是 SET LOCAL（事务级），
这条用例会稳定失败。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


async def test_alternating_tenants_never_leak(client_a, client_b, tenant_a, tenant_b):
    for _ in range(50):
        response_a = await client_a.get("/api/v1/auth/me")
        assert response_a.status_code == 200
        assert response_a.json()["tenant_id"] == str(tenant_a[0])

        response_b = await client_b.get("/api/v1/auth/me")
        assert response_b.status_code == 200
        assert response_b.json()["tenant_id"] == str(tenant_b[0])


async def test_tenant_visibility_stable_under_reuse(client_a, client_b, tenant_b):
    for _ in range(20):
        # A 反复请求时，任何一次都不应该看到 B 的租户
        assert (await client_a.get(f"/api/v1/tenants/{tenant_b[0]}")).status_code == 404
