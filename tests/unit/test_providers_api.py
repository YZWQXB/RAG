"""真实 Provider 的边界测试。

fake provider 只覆盖测试替身路径，生产走的是 api_bge / openai_compat。
这里用 httpx.MockTransport 在最外层替换掉网络，既不触网也能验证：
重试、SSE 解析、错误映射这些真正容易写错的部分。
（characterization：为既有实现补覆盖率，不涉及生产代码改动）
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.providers.embedding.api_bge import ApiBgeEmbedding
from app.providers.embedding.base import EmbeddingError
from app.providers.llm.base import LLMError
from app.providers.llm.openai_compat import OpenAICompatLLM
from app.providers.reranker.api_bge import ApiBgeReranker


def _use_transport(monkeypatch, handler) -> None:
    """把共享客户端换成带 MockTransport 的实例。

    生产代码已改为复用 `app.core.http` 里的进程级客户端，
    所以不能再 patch `httpx.AsyncClient` 构造器——那只会影响新建的实例。
    """
    from app.core import http as http_module

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http_module, "_client", client)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    import asyncio

    async def _noop(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)


# ---------------- embedding ----------------


async def test_embedding_returns_vectors(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content)
        assert body["input"] == ["a", "b"]
        return httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
        )

    _use_transport(monkeypatch, handler)
    assert await ApiBgeEmbedding().embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


async def test_embedding_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    _use_transport(monkeypatch, handler)
    assert await ApiBgeEmbedding().embed(["x"]) == [[1.0]]
    assert calls["n"] == 3


async def test_embedding_gives_up_after_three_attempts(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")

    _use_transport(monkeypatch, lambda request: httpx.Response(500, json={}))
    with pytest.raises(EmbeddingError, match="向量化失败"):
        await ApiBgeEmbedding().embed(["x"])


async def test_embedding_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "")
    with pytest.raises(EmbeddingError, match="EMBEDDING_API_KEY"):
        await ApiBgeEmbedding().embed(["x"])


async def test_embedding_rejects_count_mismatch(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    _use_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"data": [{"embedding": [1.0]}]})
    )
    with pytest.raises(EmbeddingError, match="向量数量"):
        await ApiBgeEmbedding().embed(["a", "b"])


async def test_embedding_empty_input_skips_network(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "")
    assert await ApiBgeEmbedding().embed([]) == []


# ---------------- llm ----------------


def _sse_response(chunks: list[str]) -> httpx.Response:
    body = "".join(chunks)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


async def test_llm_stream_parses_deltas(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    body = [
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    _use_transport(monkeypatch, lambda request: _sse_response(body))

    parts = [
        delta.text async for delta in OpenAICompatLLM().stream([{"role": "user", "content": "hi"}])
    ]
    assert "".join(parts) == "你好"


async def test_llm_stream_ignores_malformed_chunks(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    body = [
        "data: {not json}\n\n",
        "data: \n\n",
        'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        "data: [DONE]\n\n",
    ]
    _use_transport(monkeypatch, lambda request: _sse_response(body))
    parts = [
        delta.text async for delta in OpenAICompatLLM().stream([{"role": "user", "content": "hi"}])
    ]
    assert "".join(parts) == "ok"


async def test_llm_non_200_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    _use_transport(monkeypatch, lambda request: httpx.Response(503, json={}))

    with pytest.raises(LLMError, match="503"):
        async for _ in OpenAICompatLLM().stream([{"role": "user", "content": "hi"}]):
            pass


async def test_llm_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        async for _ in OpenAICompatLLM().stream([{"role": "user", "content": "hi"}]):
            pass


# ---------------- reranker ----------------


async def test_reranker_maps_scores_by_index(monkeypatch):
    monkeypatch.setattr(settings, "reranker_api_key", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.5},
                ]
            },
        )

    _use_transport(monkeypatch, handler)
    scores = await ApiBgeReranker().rerank("q", ["a", "b", "c"])
    assert scores == [0.1, 0.5, 0.9]


async def test_reranker_empty_documents(monkeypatch):
    monkeypatch.setattr(settings, "reranker_api_key", "test-key")
    assert await ApiBgeReranker().rerank("q", []) == []


async def test_reranker_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "reranker_api_key", "")
    with pytest.raises(Exception, match="RERANKER_API_KEY"):
        await ApiBgeReranker().rerank("q", ["a"])
