from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


async def test_can_read_own_tenant(client_a, tenant_a):
    tenant_id = tenant_a[0]
    response = await client_a.get(f"/api/v1/tenants/{tenant_id}")
    assert response.status_code == 200
    assert response.json()["slug"] == "tenant-a"


async def test_cannot_read_other_tenant_returns_404(client_a, tenant_b):
    """必须是 404 而非 403：403 等于告诉对方"这个资源存在"。"""
    response = await client_a.get(f"/api/v1/tenants/{tenant_b[0]}")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "资源不存在"


async def test_error_body_has_no_traceback(client_a, tenant_b):
    response = await client_a.get(f"/api/v1/tenants/{tenant_b[0]}")
    raw = response.text
    assert "Traceback" not in raw
    assert "asyncpg" not in raw


async def test_request_without_token_is_401(anon_client):
    response = await anon_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_returns_own_tenant(client_a, tenant_a):
    response = await client_a.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(tenant_a[0])
