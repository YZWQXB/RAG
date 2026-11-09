"""向量化与块写入。写入按 (document_id, version_id, chunk_index) 幂等 upsert。"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestion.models import Chunk
from app.modules.ingestion.pipeline.chunk import ChunkDraft
from app.providers.embedding.base import EmbeddingProvider

BATCH_SIZE = 32


async def write_chunks(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    drafts: list[ChunkDraft],
    vectors: list[list[float]],
) -> int:
    if not drafts:
        return 0

    rows = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "kb_id": kb_id,
            "document_id": document_id,
            "version_id": version_id,
            "chunk_index": draft.chunk_index,
            "content": draft.content,
            "token_count": draft.token_count,
            "heading_path": draft.heading_path,
            "page_from": draft.page_from,
            "page_to": draft.page_to,
            "content_hash": draft.content_hash,
            "embedding": vector,
        }
        for draft, vector in zip(drafts, vectors, strict=True)
    ]

    stmt = pg_insert(Chunk).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_chunks_pos",
        set_={
            "content": stmt.excluded.content,
            "token_count": stmt.excluded.token_count,
            "heading_path": stmt.excluded.heading_path,
            "page_from": stmt.excluded.page_from,
            "page_to": stmt.excluded.page_to,
            "content_hash": stmt.excluded.content_hash,
            "embedding": stmt.excluded.embedding,
        },
    )
    # synchronize_session=False：ON CONFLICT 的 affected rows 与 ORM 预期不一致，
    # 保持默认会让 SQLAlchemy 抛 StaleDataError。
    await session.execute(stmt.execution_options(synchronize_session=False))

    # 重跑后块数变少时，清掉上一版残留的尾部块
    await session.execute(
        delete(Chunk)
        .where(
            Chunk.document_id == document_id,
            Chunk.version_id == version_id,
            Chunk.chunk_index >= len(drafts),
        )
        .execution_options(synchronize_session=False)
    )
    return len(rows)


async def embed_drafts(provider: EmbeddingProvider, drafts: list[ChunkDraft]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(drafts), BATCH_SIZE):
        batch = drafts[start : start + BATCH_SIZE]
        batch_vectors = await provider.embed([draft.content for draft in batch])
        vectors.extend(batch_vectors)
    return vectors
