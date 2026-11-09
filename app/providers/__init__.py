"""Provider 工厂与测试替身注入点。

业务代码只调用 get_xxx_provider()，不直接 new 具体实现，也不直接发 HTTP。
测试通过 set_xxx_provider() 注入确定性替身，跑完 reset_providers() 复原。
"""

from __future__ import annotations

from app.core.config import settings
from app.providers.embedding.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.reranker.base import RerankerProvider

_overrides: dict[str, object] = {}


def set_embedding_provider(provider: EmbeddingProvider) -> None:
    _overrides["embedding"] = provider


def set_llm_provider(provider: LLMProvider) -> None:
    _overrides["llm"] = provider


def set_reranker_provider(provider: RerankerProvider) -> None:
    _overrides["reranker"] = provider


def reset_providers() -> None:
    _overrides.clear()


def get_embedding_provider() -> EmbeddingProvider:
    if "embedding" in _overrides:
        return _overrides["embedding"]  # type: ignore[return-value]
    if settings.embedding_provider == "fake":
        from app.providers.embedding.fake import FakeEmbedding

        return FakeEmbedding()
    if settings.embedding_provider == "local":
        from app.providers.embedding.local_bge import LocalBgeEmbedding

        return LocalBgeEmbedding()
    from app.providers.embedding.api_bge import ApiBgeEmbedding

    return ApiBgeEmbedding()


def get_llm_provider() -> LLMProvider:
    if "llm" in _overrides:
        return _overrides["llm"]  # type: ignore[return-value]
    if settings.llm_provider == "fake":
        from app.providers.llm.fake import FakeLLM

        return FakeLLM()
    from app.providers.llm.openai_compat import OpenAICompatLLM

    return OpenAICompatLLM()


def get_reranker_provider() -> RerankerProvider:
    if "reranker" in _overrides:
        return _overrides["reranker"]  # type: ignore[return-value]
    if settings.reranker_provider == "fake" or not settings.retrieval_rerank_enabled:
        from app.providers.reranker.fake import FakeReranker

        return FakeReranker()
    from app.providers.reranker.api_bge import ApiBgeReranker

    return ApiBgeReranker()
