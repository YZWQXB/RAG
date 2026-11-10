"""进程内本地向量化（sentence-transformers + bge-m3）。"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.embedding.base import EmbeddingError

log = get_logger("embedding")

_loaded_models: dict[tuple[str, str], Any] = {}
_load_lock = asyncio.Lock()


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _load_model_sync(model_name: str, device: str) -> Any:
    """同步加载模型，供线程池调用。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的可读报错
        raise EmbeddingError(
            "local 向量模式需要额外依赖：uv sync --extra local-embedding（约 2.5GB）"
        ) from exc

    # HF 直连在国内通常不通，默认走镜像；用户显式设了 HF_ENDPOINT 就以用户为准。
    endpoint = settings.hf_endpoint.strip()
    if endpoint and not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = endpoint

    log.info("embedding_model_loading", model=model_name, device=device)
    model = SentenceTransformer(model_name, device=device)
    # sentence-transformers 5.x 把 get_sentence_embedding_dimension 改名为 get_embedding_dimension，
    # 两个版本都要能跑（项目里的版本约束允许 3.x ~ 5.x）
    dim_getter = getattr(model, "get_embedding_dimension", None) or (
        model.get_sentence_embedding_dimension
    )
    log.info("embedding_model_ready", model=model_name, dim=dim_getter())
    return model


class LocalBgeEmbedding:
    """本地 bge-m3：延迟加载 + 线程池推理 + L2 归一化。"""

    def __init__(self) -> None:
        self.model_name = settings.embedding_model
        self.device = settings.embedding_local_device
        self.dim = settings.embedding_dim
        self.batch_size = settings.embedding_local_batch_size
        self.calls = 0

    async def _get_model(self) -> Any:
        key = (self.model_name, self.device)
        cached = _loaded_models.get(key)
        if cached is not None:
            return cached
        async with _load_lock:
            # 双检：等锁期间可能已被另一个协程加载完
            cached = _loaded_models.get(key)
            if cached is not None:
                return cached
            try:
                model = await asyncio.to_thread(_load_model_sync, self.model_name, self.device)
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingError(f"本地向量模型加载失败：{type(exc).__name__}") from exc
            _loaded_models[key] = model
            return model

    def _encode_sync(self, model: Any, texts: list[str]) -> list[list[float]]:
        vectors = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in vectors]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._get_model()
        self.calls += 1

        try:
            vectors = await asyncio.to_thread(self._encode_sync, model, texts)
        except Exception as exc:
            raise EmbeddingError(f"本地向量化失败：{type(exc).__name__}") from exc

        if len(vectors) != len(texts):
            raise EmbeddingError("向量数量与输入不一致")
        if vectors and len(vectors[0]) != self.dim:
            # 维度不一致意味着索引与查询不在同一向量空间，必须立刻失败而不是静默算错。
            raise EmbeddingError(
                f"向量维度不符：模型输出 {len(vectors[0])}，配置 EMBEDDING_DIM={self.dim}"
            )
        return [_normalize(vector) for vector in vectors]
