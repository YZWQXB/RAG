from __future__ import annotations

from typing import Protocol


class RerankerError(Exception):
    pass


class RerankerProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...
