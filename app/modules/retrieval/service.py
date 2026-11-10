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
from app.modules.retrieval.lexical import lexical_recall
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
    vector: list[float] | None = None,
) -> RetrievalResult:
    """检索编排。

    vector 可由调用方预计算并传入：向量推理是 CPU 密集操作（本地 bge-m3 单条约 1~2 秒），
    放在数据库事务里会长时间占用连接池。调用方应在事务外算好后传入。
    """
    normalized = normalize_query(query)
    timings: dict[str, int] = {}

    readable = set(await filter_readable_kbs(session, principal))
    effective = [kb_id for kb_id in kb_ids if kb_id in readable]
    if not effective:
        return RetrievalResult(normalized_query=normalized, timings_ms=timings)

    started = time.perf_counter()
    if settings.embedding_provider == "fake":
        # 离线：库里存的是别的模型算的向量，余弦没有意义，直接按文本做词法打分
        dense = await lexical_recall(session, principal.tenant_id, effective, normalized)
        timings["lexical"] = int((time.perf_counter() - started) * 1000)
    else:
        if vector is None:
            vector = await embed_cached(normalized)
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
        top_sparse_score=sparse[0].score if sparse else 0.0,
        timings_ms=timings,
    )


def answerable(top_dense: float, top_sparse: float) -> bool:
    """是否具备作答依据。

    两路召回都要算数：
    - 稠密（语义）低分但稀疏（字面/trigram）高分的查询很常见——精确编号、
      长尾专有名词都属于这一类，只看稠密会误拒。
    - fake 向量与 bge-m3 的分数分布不同，阈值必须分开取。
    """
    dense_threshold = (
        settings.retrieval_no_answer_threshold_fake
        if settings.embedding_provider == "fake"
        else settings.retrieval_no_answer_threshold
    )
    return top_dense >= dense_threshold or top_sparse >= settings.retrieval_sparse_answer_threshold
