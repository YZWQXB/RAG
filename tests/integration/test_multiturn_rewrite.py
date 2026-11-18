"""多轮追问的端到端行为。

用「改写器替身」而不是真模型：本用例要验证的是**编排正确性**——
改写是否只在「原句检索不到依据」时触发、改写后是否真的重试检索、
失败时是否退回原句——而不是模型改写得好不好，后者由
`scripts.eval_rewrite` 用标注数据衡量。
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.providers import set_llm_provider
from app.providers.llm.base import LLMDelta, LLMError
from tests.helpers import basis, seed_document

pytestmark = pytest.mark.db

# 内容要短而密集：离线词法打分对「短问句 vs 长分块」很苛刻，
# 块写太长分数会被稀释，测出来的就不是编排而是打分公式了
CONTENT = "工作温度范围 5℃ 至 40℃，仅限室内环境使用。"
REWRITTEN = "设备的工作温度范围是多少"


class _RewritingLLM:
    """识别系统提示词：改写请求返回 REWRITTEN，生成请求返回固定答案。"""

    def __init__(self, *, rewrite_error: Exception | None = None, rewrite_text: str = REWRITTEN):
        self.rewrite_error = rewrite_error
        self.rewrite_text = rewrite_text
        self.rewrite_calls = 0
        self.generate_calls = 0

    async def stream(self, messages: list[dict], **kwargs):
        system = messages[0].get("content", "") if messages else ""
        if "改写器" in system:
            self.rewrite_calls += 1
            if self.rewrite_error:
                raise self.rewrite_error
            yield LLMDelta(text=self.rewrite_text)
            return
        self.generate_calls += 1
        yield LLMDelta(text="工作温度 5℃ 至 40℃ [1]。")


def _offline_retrieval(monkeypatch) -> None:
    """显式走离线词法路径。

    .env 里可能是 EMBEDDING_PROVIDER=local，此时检索走 pgvector，
    而测试播种的向量与假 embedding 的查询向量不在同一空间，会直接拒答。
    这里钉住 fake，让召回只依赖文本重合，测试才不随 .env 漂移。
    """
    monkeypatch.setattr(settings, "embedding_provider", "fake")
    # 本用例验证的是「改写有没有接进链路」，不是阈值本身，因此放宽离线阈值
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold_fake", 0.05)


async def _new_conversation(client, kb_id) -> str:
    """显式建会话，避免依赖 done 事件字段——no_answer 分支不返回 conversation_id。"""
    created = await client.post("/api/v1/conversations", json={"kb_ids": [str(kb_id)]})
    assert created.status_code == 201
    return created.json()["id"]


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


def _citations(events: list[tuple[str, dict]]) -> dict | None:
    return next((data for name, data in events if name == "citations"), None)


@pytest.fixture
def rewriting(monkeypatch):
    monkeypatch.setattr(settings, "enable_llm_rewrite", True)
    monkeypatch.setattr(settings, "llm_provider", "api")
    _offline_retrieval(monkeypatch)
    llm = _RewritingLLM()
    set_llm_provider(llm)
    return llm


async def test_unretrievable_followup_is_rewritten(client_a, tenant_a, low_threshold, rewriting):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, basis(500))])
    conversation_id = await _new_conversation(client_a, kb_id)

    # 先有一轮对话：没有历史的话门控按设计就不改写，测不到改写逻辑
    await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "设备能在室外用吗"},
    )
    assert rewriting.rewrite_calls == 0, "首轮不应触发改写"

    # 「那它呢」在原句下没有任何实词重合，检索必然失败 → 这时才改写
    events = await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "那它呢"},
    )

    citations = _citations(events)
    assert citations is not None, "改写后应能检索到依据并作答"
    assert citations["rewritten_query"] == REWRITTEN
    assert citations["citations"], "重试检索应召回分块"
    assert rewriting.rewrite_calls == 1


async def test_retrievable_followup_skips_rewrite(client_a, tenant_a, low_threshold, rewriting):
    """追问本身就能检索到时不该改写：常规提问必须零额外延迟与零 token。"""
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, basis(500))])
    conversation_id = await _new_conversation(client_a, kb_id)

    await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "设备能在室外用吗"},
    )

    events = await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "那温度范围呢"},
    )

    citations = _citations(events)
    assert citations is not None
    assert citations["rewritten_query"] is None
    assert rewriting.rewrite_calls == 0


async def test_rewrite_failure_falls_back_to_refusal(
    client_a, tenant_a, low_threshold, monkeypatch
):
    monkeypatch.setattr(settings, "enable_llm_rewrite", True)
    monkeypatch.setattr(settings, "llm_provider", "api")
    _offline_retrieval(monkeypatch)
    set_llm_provider(_RewritingLLM(rewrite_error=LLMError("上游 500")))

    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, basis(500))])
    conversation_id = await _new_conversation(client_a, kb_id)

    # 历史与追问都与其他用例不同：改写结果缓存在 Redis 里，
    # 若问句+上下文完全一致会命中缓存，测出来的就不是「失败兜底」了
    await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "室外能用吗"},
    )
    events = await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "那这个呢"},
    )

    # 改写失败不能变成问答失败：退回原句，该拒答就拒答
    names = [name for name, _ in events]
    assert names[0] == "no_answer"
    assert names[-1] == "done"


async def test_first_turn_is_never_rewritten(client_a, tenant_a, low_threshold, rewriting):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, basis(500))])

    events = await _events(client_a, {"kb_ids": [str(kb_id)], "query": "温度范围是多少"})

    citations = _citations(events)
    assert citations is not None
    assert citations["rewritten_query"] is None
    assert rewriting.rewrite_calls == 0


async def test_self_contained_followup_is_not_rewritten(
    client_a, tenant_a, low_threshold, rewriting
):
    """第二轮也可能自带主语，这时多花一次 LLM 调用纯属浪费。"""
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, basis(500))])
    conversation_id = await _new_conversation(client_a, kb_id)

    await _events(
        client_a,
        {"kb_ids": [str(kb_id)], "conversation_id": conversation_id, "query": "设备能在室外用吗"},
    )
    events = await _events(
        client_a,
        {
            "kb_ids": [str(kb_id)],
            "conversation_id": conversation_id,
            "query": "设备的工作温度范围是 5 到 40 摄氏度吗",
        },
    )

    citations = _citations(events)
    assert citations is not None
    assert citations["rewritten_query"] is None
    assert rewriting.rewrite_calls == 0
