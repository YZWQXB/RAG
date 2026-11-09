from __future__ import annotations

from typing import Protocol


class EmbeddingError(Exception):
    """向量化失败的统一异常，由 pipeline 转成可读错误。"""


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
