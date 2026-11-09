from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


async def test_create_and_list_kb(client_a):
    created = await client_a.post("/api/v1/kbs", json={"name": "产品手册"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "产品手册"
    assert body["document_count"] == 0

    listed = await client_a.get("/api/v1/kbs")
    assert listed.status_code == 200
    assert [kb["id"] for kb in listed.json()] == [body["id"]]


async def test_create_kb_rejects_blank_name(client_a):
    response = await client_a.post("/api/v1/kbs", json={"name": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_NAME"
    assert response.json()["error"]["message"] == "知识库名称不能为空"


async def test_viewer_cannot_create_kb(viewer_client_a):
    response = await viewer_client_a.post("/api/v1/kbs", json={"name": "越权"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_update_and_soft_delete(client_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "旧名"})).json()
    patched = await client_a.patch(f"/api/v1/kbs/{kb['id']}", json={"name": "新名"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "新名"

    deleted = await client_a.delete(f"/api/v1/kbs/{kb['id']}")
    assert deleted.status_code == 204

    assert (await client_a.get(f"/api/v1/kbs/{kb['id']}")).status_code == 404
    assert (await client_a.get("/api/v1/kbs")).json() == []


async def test_editor_sees_only_granted_kb(editor_client_a, client_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "仅授权"})).json()

    # 未授权时 editor 看不到
    assert (await editor_client_a.get("/api/v1/kbs")).json() == []
    denied = await editor_client_a.get(f"/api/v1/kbs/{kb['id']}")
    assert denied.status_code == 403

    # 授权 read 后可见
    grant = await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={"subject_type": "role", "subject_id": "editor", "permission": "read"},
    )
    assert grant.status_code == 200
    assert [item["id"] for item in (await editor_client_a.get("/api/v1/kbs")).json()] == [kb["id"]]
