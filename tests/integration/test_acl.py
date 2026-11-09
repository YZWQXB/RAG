from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


async def test_grant_read_then_write_denied(client_a, editor_client_a, tenant_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "授权测试"})).json()

    await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={"subject_type": "role", "subject_id": "editor", "permission": "read"},
    )
    assert (await editor_client_a.get(f"/api/v1/kbs/{kb['id']}")).status_code == 200
    assert (
        await editor_client_a.patch(f"/api/v1/kbs/{kb['id']}", json={"name": "改名"})
    ).status_code == 403


async def test_grant_manage_allows_update(client_a, editor_client_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "管理授权"})).json()
    await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={"subject_type": "role", "subject_id": "editor", "permission": "manage"},
    )
    response = await editor_client_a.patch(f"/api/v1/kbs/{kb['id']}", json={"name": "已改"})
    assert response.status_code == 200
    assert response.json()["name"] == "已改"


async def test_acl_rejects_foreign_user(client_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "越界授权"})).json()
    response = await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={
            "subject_type": "user",
            "subject_id": "00000000-0000-0000-0000-000000000001",
            "permission": "read",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SUBJECT"


async def test_acl_list_and_delete(client_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "ACL 列表"})).json()
    created = await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={"subject_type": "role", "subject_id": "viewer", "permission": "read"},
    )
    acl_id = created.json()["id"]

    listed = await client_a.get(f"/api/v1/kbs/{kb['id']}/acl")
    assert len(listed.json()) == 1

    assert (await client_a.delete(f"/api/v1/kbs/{kb['id']}/acl/{acl_id}")).status_code == 204
    assert (await client_a.get(f"/api/v1/kbs/{kb['id']}/acl")).json() == []


async def test_remove_member_clears_acl(client_a, editor_client_a, tenant_a):
    members = (await client_a.get("/api/v1/members")).json()
    editor = next(m for m in members if m["email"] == "editor-a@example.com")

    kb = (await client_a.post("/api/v1/kbs", json={"name": "残留权限"})).json()
    await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={"subject_type": "user", "subject_id": editor["user_id"], "permission": "read"},
    )

    assert (await client_a.delete(f"/api/v1/members/{editor['user_id']}")).status_code == 204

    from app.core.db import session_scope

    async with session_scope(tenant_id=tenant_a[0]) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM kb_acl WHERE subject_id = :sid"),
                {"sid": editor["user_id"]},
            )
        ).scalar()
    assert count == 0


async def test_cannot_modify_owner(client_a, tenant_a):
    owner_id = tenant_a[1]
    assert (
        await client_a.patch(f"/api/v1/members/{owner_id}", json={"role": "viewer"})
    ).status_code == 403
    assert (await client_a.delete(f"/api/v1/members/{owner_id}")).status_code == 403
