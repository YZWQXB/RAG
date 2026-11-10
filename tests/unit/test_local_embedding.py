"""本地向量 Provider 的单元测试。

不加载真实 bge-m3（2.3GB），统一用替身模型验证四件容易写错的事：
空输入、归一化、维度不符的硬失败、模型只加载一次。
"""

from __future__ import annotations

import math

import pytest

from app.core.config import settings
from app.providers.embedding import local_bge
from app.providers.embedding.base import EmbeddingError
from app.providers.embedding.local_bge import LocalBgeEmbedding


class _FakeModel:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.encode_calls = 0

    def get_sentence_embedding_dimension(self) -> int:
        return self.dim

    def encode(self, texts, **_kwargs):
        self.encode_calls += 1
        return [[float(i + 1)] * self.dim for i, _ in enumerate(texts)]


@pytest.fixture(autouse=True)
def _clear_model_cache():
    local_bge._loaded_models.clear()
    yield
    local_bge._loaded_models.clear()


def _patch_loader(monkeypatch, model: _FakeModel) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def loader(name: str, device: str):
        calls.append((name, device))
        return model

    monkeypatch.setattr(local_bge, "_load_model_sync", loader)
    return calls


@pytest.mark.asyncio
async def test_empty_input_skips_model(monkeypatch):
    calls = _patch_loader(monkeypatch, _FakeModel())
    provider = LocalBgeEmbedding()
    assert await provider.embed([]) == []
    assert calls == []


@pytest.mark.asyncio
async def test_vectors_are_normalized(monkeypatch):
    monkeypatch.setattr(settings, "embedding_dim", 4)
    _patch_loader(monkeypatch, _FakeModel(dim=4))

    vectors = await LocalBgeEmbedding().embed(["甲", "乙"])
    assert len(vectors) == 2
    for vector in vectors:
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-6)


@pytest.mark.asyncio
async def test_dimension_mismatch_fails_loudly(monkeypatch):
    """维度不符必须报错：静默通过会让查询向量和索引向量永远算不出相似度。"""
    monkeypatch.setattr(settings, "embedding_dim", 1024)
    _patch_loader(monkeypatch, _FakeModel(dim=4))

    with pytest.raises(EmbeddingError, match="维度不符"):
        await LocalBgeEmbedding().embed(["甲"])


@pytest.mark.asyncio
async def test_model_loaded_once_across_calls(monkeypatch):
    monkeypatch.setattr(settings, "embedding_dim", 4)
    model = _FakeModel(dim=4)
    calls = _patch_loader(monkeypatch, model)

    provider = LocalBgeEmbedding()
    await provider.embed(["甲"])
    await provider.embed(["乙"])
    assert len(calls) == 1
    assert model.encode_calls == 2


@pytest.mark.asyncio
async def test_load_failure_wrapped(monkeypatch):
    def boom(_name: str, _device: str):
        raise RuntimeError("镜像不通")

    monkeypatch.setattr(local_bge, "_load_model_sync", boom)

    with pytest.raises(EmbeddingError, match="加载失败"):
        await LocalBgeEmbedding().embed(["甲"])


@pytest.mark.asyncio
async def test_provider_factory_returns_local(monkeypatch):
    from app.providers import get_embedding_provider, reset_providers

    reset_providers()
    monkeypatch.setattr(settings, "embedding_provider", "local")
    assert isinstance(get_embedding_provider(), LocalBgeEmbedding)


def test_missing_dependency_message_is_actionable(monkeypatch):
    """依赖缺失时的报错必须给出可执行的安装命令，而不是裸 ImportError。"""

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(EmbeddingError) as exc:
        local_bge._load_model_sync("BAAI/bge-m3", "cpu")
    assert "local-embedding" in str(exc.value)
