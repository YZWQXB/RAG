"""OpenAI 兼容的 embeddings 接口（硅基流动 / 通义 等）。"""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.embedding.base import EmbeddingError

log = get_logger("embedding")


class ApiBgeEmbedding:
    def __init__(self) -> None:
        self.base_url = settings.embedding_base_url.rstrip("/")
        self.api_key = settings.embedding_api_key
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("未配置 EMBEDDING_API_KEY")

        backoff = [1, 4, 16]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.base_url}/embeddings",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "input": texts},
                    )
                    response.raise_for_status()
                    payload = response.json()
                vectors = [item["embedding"] for item in payload["data"]]
                if len(vectors) != len(texts):
                    raise EmbeddingError("向量数量与输入不一致")
                return vectors
            except EmbeddingError:
                # 数据层面的问题（返回条数不符）重试没有意义，直接上抛保留原始信息
                raise
            except Exception as exc:  # 网络/限流/格式错误才重试
                last_error = exc
                log.warning("embedding_retry", attempt=attempt + 1, error=type(exc).__name__)
                if attempt < 2:
                    await asyncio.sleep(backoff[attempt])

        raise EmbeddingError(f"向量化失败：{type(last_error).__name__}")
