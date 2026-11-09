"""稠密召回：pgvector 余弦相似度。

租户与 KB 过滤写在 SQL 内（不依赖 RLS 单点），并排除已删除文档。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ingestion.models import Chunk, Document
from app.modules.retrieval.types import Candidate


async def dense_recall(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    vector: list[float],
    top_k: int | None = None,
) -> list[Candidate]:
    if not kb_ids:
        return []

    limit = top_k or settings.retrieval_top_dense
    await session.execute(text("SELECT set_config('hnsw.ef_search', '100', true)"))

    distance = Chunk.embedding.cosine_distance(vector)
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
            (1 - distance).label("score"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.tenant_id == tenant_id,
            Chunk.kb_id.in_(kb_ids),
            Document.deleted_at.is_(None),
        )
        .order_by(distance)
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
