from __future__ import annotations

import json

import pytest

from tests.helpers import basis, fake_vector, seed_document

pytestmark = pytest.mark.db


async def _events(client, payload: dict) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    async with client.stream("POST", "/api/v1/chat/stream", json=payload) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event_name = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:") and event_name:
                events.append((event_name, json.loads(line[5:].strip())))
                event_name = None
    return events


async def test_stream_event_order_and_content(client_a, tenant_a, low_threshold):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id,
        user_id,
        [("员工入职满一年可享受 5 天年假。", await fake_vector("员工入职满一年可享受 5 天年假。"))],
    )

    events = await _events(client_a, {"kb_ids": [str(kb_id)], "query": "年假有多少天"})
    names = [name for name, _ in events]

    assert names[0] == "citations"
    assert "token" in names
    assert names[-1] == "done"

    answer = "".join(data["t"] for name, data in events if name == "token")
    assert answer
    assert events[-1][1]["used_citations"]


async def test_no_answer_when_no_documents(client_a, tenant_a):
    tenant_id, user_id, _ = tenant_a
    import uuid

    from app.core.db import session_scope
    from app.modules.knowledge.models import KnowledgeBase

    kb_id = uuid.uuid4()
    async with session_scope(tenant_id=tenant_id) as session:
        session.add(
            KnowledgeBase(
                id=kb_id, tenant_id=tenant_id, name="空库", chunk_config={}, created_by=user_id
            )
        )
        await session.flush()

    events = await _events(client_a, {"kb_ids": [str(kb_id)], "query": "年假有多少天"})
    names = [name for name, _ in events]
    assert names[0] == "no_answer"
    assert "现有资料未涵盖该问题" in events[0][1]["message"]
    assert names[-1] == "done"


async def test_no_answer_does_not_call_llm(client_a, tenant_a):
    import uuid

    from app.core.db import session_scope
    from app.modules.knowledge.models import KnowledgeBase
    from app.providers import set_llm_provider
    from app.providers.llm.fake import FakeLLM

    spy = FakeLLM()
    set_llm_provider(spy)

    tenant_id, user_id, _ = tenant_a
    kb_id = uuid.uuid4()
    async with session_scope(tenant_id=tenant_id) as session:
        session.add(
            KnowledgeBase(
                id=kb_id, tenant_id=tenant_id, name="空库", chunk_config={}, created_by=user_id
            )
        )
        await session.flush()

    await _events(client_a, {"kb_ids": [str(kb_id)], "query": "无关问题"})
    assert spy.calls == 0


async def test_low_score_triggers_no_answer(client_a, tenant_a):
    tenant_id, user_id, _ = tenant_a
    # 块向量与查询向量正交 → 余弦相似度 0，低于阈值 0.35
    kb_id, _, _ = await seed_document(tenant_id, user_id, [("完全无关的内容", basis(500))])

    events = await _events(client_a, {"kb_ids": [str(kb_id)], "query": "年假有多少天"})
    assert events[0][0] == "no_answer"


async def test_no_access_to_kb_returns_403_body(client_a, tenant_b):
    # 流式响应开始后无法改状态码，因此可预判的失败（无权限/超配额）在路由层直接返回
    response = await client_a.post(
        "/api/v1/chat/stream",
        json={"kb_ids": [str(tenant_b[0])], "query": "x"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_llm_failure_emits_error(client_a, tenant_a, low_threshold):
    from app.providers import set_llm_provider
    from app.providers.llm.fake import FakeLLM

    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id,
        user_id,
        [("员工入职满一年可享受 5 天年假。", await fake_vector("员工入职满一年可享受 5 天年假。"))],
    )
    set_llm_provider(FakeLLM(raise_after=2))

    events = await _events(client_a, {"kb_ids": [str(kb_id)], "query": "年假"})
    names = [name for name, _ in events]
    assert "token" in names
    assert names[-1] == "error"
