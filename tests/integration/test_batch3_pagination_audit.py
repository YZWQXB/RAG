"""分页与审计补全。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db


async def _kb(client_a, tenant_a) -> str:
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [("年假 5 天。", await fake_vector("年假 5 天。"))]
    )
    return str(kb_id)


async def _create(client, kb_id: str, title: str) -> dict:
    response = await client.post("/api/v1/conversations", json={"kb_ids": [kb_id], "title": title})
    assert response.status_code == 201
    return response.json()


async def test_conversations_support_limit_and_offset(client_a, tenant_a):
    kb_id = await _kb(client_a, tenant_a)
    for index in range(3):
        await _create(client_a, kb_id, f"会话-{index}")

    page1 = await client_a.get("/api/v1/conversations", params={"limit": 2})
    page2 = await client_a.get("/api/v1/conversations", params={"limit": 2, "offset": 2})

    assert page1.status_code == 200
    assert len(page1.json()) == 2
    assert len(page2.json()) == 1
    assert {item["id"] for item in page1.json()}.isdisjoint({item["id"] for item in page2.json()})


async def test_messages_limit_returns_latest_in_ascending_order(client_a, tenant_a):
    tenant_id, user_id, _ = tenant_a
    kb_id = await _kb(client_a, tenant_a)
    conversation = await _create(client_a, kb_id, "长会话")

    base = datetime.now(UTC)
    async with session_scope(tenant_id=tenant_id) as session:
        for index in range(5):
            await session.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content, status, "
                    "token_in, token_out, latency_ms, created_at) "
                    "VALUES (:i, :t, :c, 'user', :content, 'normal', 0, 0, 0, :at)"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": tenant_id,
                    "c": conversation["id"],
                    "content": f"第 {index} 条",
                    "at": base + timedelta(seconds=index),
                },
            )

    response = await client_a.get(
        f"/api/v1/conversations/{conversation['id']}/messages", params={"limit": 2}
    )
    assert response.status_code == 200
    contents = [item["content"] for item in response.json()]
    assert contents == ["第 3 条", "第 4 条"], "应取最近两条，且保持时间正序"


async def test_login_writes_audit_log(anon_client):
    email = f"audit-{uuid.uuid4().hex[:8]}@example.com"
    registered = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Passw0rd!2026", "tenant_name": "审计空间"},
    )
    assert registered.status_code == 201

    login = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Passw0rd!2026"}
    )
    assert login.status_code == 200
    token = login.json()["token"]

    logs = await anon_client.get(
        "/api/v1/audit-logs",
        params={"action": "auth.login"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logs.status_code == 200
    assert any(item["action"] == "auth.login" for item in logs.json())


async def test_member_changes_are_audited(client_a, anon_client):
    # 成员必须是已注册用户（产品暂不支持邮件邀请），先建号再加入空间
    email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    registered = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Passw0rd!2026", "tenant_name": "待加入空间"},
    )
    assert registered.status_code == 201

    added = await client_a.post("/api/v1/members", json={"email": email, "role": "viewer"})
    assert added.status_code == 201
    member_id = added.json()["user_id"]

    logs = await client_a.get("/api/v1/audit-logs", params={"action": "member.add"})
    assert any(item["target_id"] == member_id for item in logs.json())

    patched = await client_a.patch(f"/api/v1/members/{member_id}", json={"role": "editor"})
    assert patched.status_code == 200
    logs = await client_a.get("/api/v1/audit-logs", params={"action": "member.update"})
    assert any(item["target_id"] == member_id for item in logs.json())
