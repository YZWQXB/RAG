from __future__ import annotations

import pytest

from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db


async def _ask(client, kb_id, query, conversation_id=None):
    payload = {"kb_ids": [str(kb_id)], "query": query}
    if conversation_id:
        payload["conversation_id"] = str(conversation_id)
    async with client.stream("POST", "/api/v1/chat/stream", json=payload) as response:
        async for _ in response.aiter_lines():
            pass


async def test_conversation_created_and_titled(client_a, tenant_a):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )
    await _ask(client_a, kb_id, "年假有多少天")

    conversations = (await client_a.get("/api/v1/conversations")).json()
    assert len(conversations) == 1
    assert conversations[0]["title"] == "年假有多少天"
    assert conversations[0]["message_count"] == 2


async def test_messages_and_citations_persisted(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )
    await _ask(client_a, kb_id, "年假有多少天")

    conversation_id = (await client_a.get("/api/v1/conversations")).json()[0]["id"]
    messages = (await client_a.get(f"/api/v1/conversations/{conversation_id}/messages")).json()

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant["status"] == "normal"
    assert assistant["citations"], "助手消息必须带引用"
    assert assistant["citations"][0]["page_from"] >= 1


async def test_continue_conversation_appends_messages(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )
    await _ask(client_a, kb_id, "年假有多少天")
    conversation_id = (await client_a.get("/api/v1/conversations")).json()[0]["id"]

    await _ask(client_a, kb_id, "那病假呢", conversation_id)
    messages = (await client_a.get(f"/api/v1/conversations/{conversation_id}/messages")).json()
    assert len(messages) == 4


async def test_create_conversation_requires_kb(client_a):
    response = await client_a.post("/api/v1/conversations", json={"kb_ids": []})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NO_KB"


async def test_delete_conversation(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [("内容", await fake_vector("内容"))])
    await _ask(client_a, kb_id, "问题")

    conversation_id = (await client_a.get("/api/v1/conversations")).json()[0]["id"]
    assert (await client_a.delete(f"/api/v1/conversations/{conversation_id}")).status_code == 204
    assert (await client_a.get("/api/v1/conversations")).json() == []
    assert (
        await client_a.get(f"/api/v1/conversations/{conversation_id}/messages")
    ).status_code == 404


async def test_cross_tenant_conversation_is_404(client_a, client_b, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [("内容", await fake_vector("内容"))])
    await _ask(client_a, kb_id, "问题")
    conversation_id = (await client_a.get("/api/v1/conversations")).json()[0]["id"]

    assert (
        await client_b.get(f"/api/v1/conversations/{conversation_id}/messages")
    ).status_code == 404
