"""稀疏召回：pg_trgm 三元组相似度。

选 trigram 而不是 BM25 的原因：zhparser/pg_bigm 需要自定义 PG 镜像，而 pg_trgm 是内置扩展。
它解决的是向量检索最弱的一类查询——专有名词、型号、编号（"SKU-8823"）。
P1 若需要更高中文召回，可替换为 zhparser BM25，接口不变。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ingestion.models import Chunk, Document
from app.modules.retrieval.types import Candidate

# 中文四字以上短语在 trigram 下相似度天然偏低，0.3 的默认阈值会过滤掉全部结果
SIMILARITY_THRESHOLD = "0.1"


async def sparse_recall(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query: str,
    top_k: int | None = None,
) -> list[Candidate]:
    cleaned = query.strip()
    if len(cleaned) < 2 or not kb_ids:
        return []

    limit = top_k or settings.retrieval_top_sparse
    await session.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :v, true)"),
        {"v": SIMILARITY_THRESHOLD},
    )

    similarity = func.similarity(Chunk.content, cleaned)
    stmt = (
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.kb_id,
            Chunk.content,
            Chunk.heading_path,
            Chunk.page_from,
            Chunk.page_to,
            Chunk.chunk_index,
            Document.title,
            similarity.label("score"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.tenant_id == tenant_id,
            Chunk.kb_id.in_(kb_ids),
            Document.deleted_at.is_(None),
            Chunk.content.op("%")(cleaned),
        )
        .order_by(similarity.desc())
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    return [
        Candidate(
            id=row.id,
            document_id=row.document_id,
            kb_id=row.kb_id,
            title=row.title,
            content=row.content,
            heading_path=row.heading_path,
            page_from=row.page_from,
            page_to=row.page_to,
            chunk_index=row.chunk_index,
            score=float(row.score),
        )
        for row in rows
    ]
