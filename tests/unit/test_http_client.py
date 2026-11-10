"""进程级共享 httpx 客户端。

每次问答都新建 AsyncClient 会重复做 TLS 握手（本机实测单次 200~600ms），
并发时还会各自占用连接。这里复用同一个客户端，靠 keep-alive 摊薄握手成本。

注意：客户端**不能**由某次请求关闭——它属于进程，生命周期与事件循环一致。
"""

from __future__ import annotations

import pytest

from app.core.http import close_http_client, get_http_client


async def test_client_is_reused():
    first = get_http_client()
    second = get_http_client()
    assert first is second
    await close_http_client()


async def test_client_recreated_after_close():
    first = get_http_client()
    await close_http_client()
    assert first.is_closed

    second = get_http_client()
    assert second is not first
    assert not second.is_closed
    await close_http_client()


async def test_close_is_idempotent():
    get_http_client()
    await close_http_client()
    await close_http_client()


async def test_llm_provider_uses_shared_accessor(monkeypatch):
    """LLM 适配层必须走共享客户端，而不是自己 new 一个。"""

    import app.providers.llm.openai_compat as module
    from app.providers.llm.base import LLMError

    called: list[int] = []

    def fake_get():
        called.append(1)
        raise LLMError("stub")

    monkeypatch.setattr(module, "get_http_client", fake_get)
    monkeypatch.setattr(module.settings, "llm_api_key", "test-key")

    provider = module.OpenAICompatLLM()
    with pytest.raises(LLMError):
        async for _ in provider.stream([{"role": "user", "content": "hi"}]):
            pass
    assert called == [1]


async def test_embedding_provider_uses_shared_accessor(monkeypatch):
    import app.providers.embedding.api_bge as module
    from app.providers.embedding.base import EmbeddingError

    called: list[int] = []

    def fake_get():
        called.append(1)
        raise RuntimeError("stub")

    async def no_sleep(_seconds: float) -> None:  # 跳过退避等待
        return None

    monkeypatch.setattr(module, "get_http_client", fake_get)
    monkeypatch.setattr(module.settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

    provider = module.ApiBgeEmbedding()
    with pytest.raises(EmbeddingError):
        await provider.embed(["hi"])
    assert called, "向量化必须走共享客户端"
    assert len(called) == 3, "网络类错误应重试 3 次"
