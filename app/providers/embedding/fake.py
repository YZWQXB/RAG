"""确定性的假向量实现（字符袋）。

设计目标：同一文本恒得同一向量；**共享字符越多的文本相似度越高**。
这一点很重要——如果假向量与文本无关（例如纯哈希随机），所有检索相关用例
都会恒等于 0 相似度，测试就失去了验证能力。

它仍然没有真正的语义能力（"汽车" 与 "轿车" 不会接近），因此不能用于质量评测。
"""

from __future__ import annotations

import hashlib
import math

from app.core.config import settings


def _bucket(char: str, dim: int) -> int:
    digest = hashlib.md5(char.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % dim


def _vector_for(text: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    for char in text:
        if char.isspace():
            continue
        vector[_bucket(char, dim)] += 1.0

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class FakeEmbedding:
    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [_vector_for(text, self.dim) for text in texts]


class BrokenEmbedding:
    """始终失败的替身，用于验证失败重试与状态机。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")
