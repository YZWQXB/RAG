"""重排：cross-encoder 精排 + 低收益场景跳过。

重排是质量增强而非可用性依赖，provider 失败时降级为原序，不能阻断问答。
"""

from __future__ import annotations

from dataclasses import replace

from app.core.logging import get_logger
from app.modules.retrieval.types import Candidate
from app.providers import get_reranker_provider

log = get_logger("rerank")

MAX_DOC_CHARS = 512
CONFIDENT_SCORE = 0.6
SHORT_QUERY_LEN = 12


def should_rerank(query: str, candidates: list[Candidate]) -> bool:
    if len(candidates) <= 3:
        return False
    if len(query) <= SHORT_QUERY_LEN and candidates[0].score >= CONFIDENT_SCORE:
        return False
    return True


async def rerank_top(query: str, candidates: list[Candidate], top_n: int = 6) -> list[Candidate]:
    if not candidates:
        return []

    try:
        scores = await get_reranker_provider().rerank(
            query, [c.content[:MAX_DOC_CHARS] for c in candidates]
        )
    except Exception as exc:
        log.warning("rerank_degraded", error=type(exc).__name__)
        return candidates[:top_n]

    if len(scores) != len(candidates):
        log.warning("rerank_score_mismatch", got=len(scores), expected=len(candidates))
        return candidates[:top_n]

    rescored = [
        replace(candidate, rerank_score=float(score))
        for candidate, score in zip(candidates, scores, strict=True)
    ]
    rescored.sort(key=lambda c: c.rerank_score, reverse=True)
    return rescored[:top_n]
