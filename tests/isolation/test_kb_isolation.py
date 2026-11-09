"""知识库维度的跨租户回归：全部必须返回 404，且错误体为中文无堆栈。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


async def test_cross_tenant_kb_operations_are_404(client_a, tenant_b):
    kb_b = (await client_a.post("/api/v1/kbs", json={"name": "A 的库"})).json()

    # 用 B 的客户端（tenant_b fixture 已存在，这里换成 B 的 client）
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    token_b = tenant_b[2]
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_b}"},
    ) as client_b:
        assert (await client_b.get(f"/api/v1/kbs/{kb_b['id']}")).status_code == 404
        assert (
            await client_b.patch(f"/api/v1/kbs/{kb_b['id']}", json={"name": "劫持"})
        ).status_code == 404
        assert (await client_b.delete(f"/api/v1/kbs/{kb_b['id']}")).status_code == 404
        assert (
            await client_b.put(
                f"/api/v1/kbs/{kb_b['id']}/acl",
                json={"subject_type": "role", "subject_id": "viewer", "permission": "read"},
            )
        ).status_code == 404


async def test_kb_list_only_contains_own_tenant(client_a, client_b, tenant_a):
    await client_a.post("/api/v1/kbs", json={"name": "A1"})
    await client_a.post("/api/v1/kbs", json={"name": "A2"})
    await client_b.post("/api/v1/kbs", json={"name": "B1"})

    a_list = (await client_a.get("/api/v1/kbs")).json()
    assert {kb["name"] for kb in a_list} == {"A1", "A2"}

    b_list = (await client_b.get("/api/v1/kbs")).json()
    assert {kb["name"] for kb in b_list} == {"B1"}


async def test_member_list_isolated(client_a, client_b, tenant_a):
    members = (await client_b.get("/api/v1/members")).json()
    assert len(members) == 1
    assert members[0]["user_id"] != str(tenant_a[1])


async def test_error_response_shape(client_a):
    response = await client_a.get("/api/v1/kbs/00000000-0000-0000-0000-000000000009")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["message"] == "资源不存在"
