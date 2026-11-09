"""检索编排。

注意：两路召回是**顺序**执行的。AsyncSession 不是并发安全的，同一会话不能并发查询。
若将来要真正并行，需要为每一路开独立会话——当前的延迟水平（本地 <100ms）不值得。
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import Principal
from app.modules.iam.permissions import filter_readable_kbs
from app.modules.retrieval.context import assemble_context
from app.modules.retrieval.dense import dense_recall
from app.modules.retrieval.embedding_cache import embed_cached
from app.modules.retrieval.fusion import rrf_fuse
from app.modules.retrieval.rerank import rerank_top, should_rerank
from app.modules.retrieval.rewrite import normalize_query
from app.modules.retrieval.sparse import sparse_recall
from app.modules.retrieval.types import RetrievalResult


async def retrieve(
    session: AsyncSession,
    principal: Principal,
    kb_ids: list[uuid.UUID],
    query: str,
    *,
    use_rerank: bool = True,
) -> RetrievalResult:
    normalized = normalize_query(query)
    timings: dict[str, int] = {}

    readable = set(await filter_readable_kbs(session, principal))
    effective = [kb_id for kb_id in kb_ids if kb_id in readable]
    if not effective:
        return RetrievalResult(normalized_query=normalized, timings_ms=timings)

    started = time.perf_counter()
    vector = await embed_cached(normalized)
    timings["embed"] = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    dense = await dense_recall(session, principal.tenant_id, effective, vector)
    timings["dense"] = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    sparse = await sparse_recall(session, principal.tenant_id, effective, normalized)
    timings["sparse"] = int((time.perf_counter() - started) * 1000)

    fused = rrf_fuse([dense, sparse], top_n=settings.retrieval_top_fused)

    final = fused
    if use_rerank and settings.retrieval_rerank_enabled and should_rerank(normalized, fused):
        started = time.perf_counter()
        final = await rerank_top(normalized, fused, settings.retrieval_top_final)
        timings["rerank"] = int((time.perf_counter() - started) * 1000)
    else:
        final = fused[: settings.retrieval_top_final]

    context = assemble_context(final)
    timings["total"] = sum(timings.values())

    return RetrievalResult(
        normalized_query=normalized,
        dense=dense,
        sparse=sparse,
        fused=fused,
        candidates=final,
        context=context,
        top_dense_score=dense[0].score if dense else 0.0,
        timings_ms=timings,
    )
