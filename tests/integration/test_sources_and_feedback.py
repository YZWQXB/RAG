"""引用原文、答案反馈、会话导出。

三件事都围绕同一个目标：让回答可核实、可追溯、可改进。
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db

CHUNK_TEXT = "员工入职满一年可享受 5 天年假，满三年后为 10 天。"


async def _events(client, payload: dict) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with client.stream("POST", "/api/v1/chat/stream", json=payload) as response:
        assert response.status_code == 200
        event_name = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:") and event_name:
                events.append((event_name, json.loads(line[5:].strip())))
                event_name = None
    return events


async def _ask(client, kb_id: str, query: str = "年假有多少天") -> tuple[str, list[dict]]:
    events = await _events(client, {"kb_ids": [kb_id], "query": query})
    message_id = next(data["message_id"] for name, data in events if name == "done")
    citations = next(data["citations"] for name, data in events if name == "citations")
    return message_id, citations


async def test_citation_carries_chunk_id_and_source_is_fetchable(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, chunk_ids = await seed_document(
        tenant_id, user_id, [(CHUNK_TEXT, await fake_vector(CHUNK_TEXT))]
    )

    _, citations = await _ask(client_a, str(kb_id))
    assert citations, "应返回引用"
    chunk_id = citations[0]["chunk_id"]
    assert chunk_id, "引用必须带 chunk_id，否则前端无法定位原文"

    response = await client_a.get(f"/api/v1/chunks/{chunk_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == CHUNK_TEXT
    assert body["title"] == "测试文档"
    assert body["page_from"] >= 1


async def test_chunk_source_rejects_other_tenant(client_a, tenant_b):
    """跨租户必须 404（不泄露存在性），而不是 403。"""
    tenant_id, user_id, _ = tenant_b
    _, _, chunk_ids = await seed_document(
        tenant_id, user_id, [("别人的资料", await fake_vector("别人的资料"))]
    )

    response = await client_a.get(f"/api/v1/chunks/{chunk_ids[0]}")
    assert response.status_code in (403, 404)


async def test_feedback_upserts_and_snapshots(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [(CHUNK_TEXT, await fake_vector(CHUNK_TEXT))]
    )
    message_id, _ = await _ask(client_a, str(kb_id))

    first = await client_a.post(
        f"/api/v1/conversations/messages/{message_id}/feedback", json={"rating": "down"}
    )
    assert first.status_code == 200
    assert first.json() == {"message_id": message_id, "rating": "down", "created": True}

    second = await client_a.post(
        f"/api/v1/conversations/messages/{message_id}/feedback",
        json={"rating": "up", "comment": "其实挺准的"},
    )
    assert second.status_code == 200
    assert second.json()["created"] is False, "同一用户应覆盖而不是插入第二条"

    from sqlalchemy import text

    from app.core.db import session_scope

    async with session_scope(tenant_id=tenant_id) as session:
        rows = (
            await session.execute(
                text("SELECT rating, snapshot FROM message_feedback WHERE message_id = :m"),
                {"m": message_id},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == "up"
    snapshot = rows[0][1]
    assert snapshot["question"] == "年假有多少天"
    assert snapshot["citations"], "快照必须包含当时的引用，便于 bad case 复盘"


async def test_feedback_rejects_unknown_message(client_a):
    response = await client_a.post(
        f"/api/v1/conversations/messages/{uuid.uuid4()}/feedback", json={"rating": "up"}
    )
    assert response.status_code == 404


async def test_feedback_rejects_invalid_rating(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [(CHUNK_TEXT, await fake_vector(CHUNK_TEXT))]
    )
    message_id, _ = await _ask(client_a, str(kb_id))

    response = await client_a.post(
        f"/api/v1/conversations/messages/{message_id}/feedback", json={"rating": "meh"}
    )
    assert response.status_code == 400


async def test_export_conversation_as_markdown(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [(CHUNK_TEXT, await fake_vector(CHUNK_TEXT))]
    )
    events = await _events(client_a, {"kb_ids": [str(kb_id)], "query": "年假有多少天"})
    conversation_id = next(
        data["conversation_id"]
        for name, data in events
        if name == "done" and data.get("conversation_id")
    )

    response = await client_a.get(f"/api/v1/conversations/{conversation_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]

    text = response.text
    assert "#" in text
    assert "年假有多少天" in text
    assert "测试文档" in text, "引用应展开成文档名"
