from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.repository import TenantScopedRepository
from app.modules.ingestion.models import Document
from app.modules.knowledge.models import KnowledgeBase


class KnowledgeBaseRepository(TenantScopedRepository):
    model = KnowledgeBase

    async def list_active(
        self, session: AsyncSession, kb_ids: list[uuid.UUID] | None = None
    ) -> list[KnowledgeBase]:
        stmt = self.scope(select(KnowledgeBase).where(KnowledgeBase.deleted_at.is_(None))).order_by(
            KnowledgeBase.updated_at.desc()
        )
        if kb_ids is not None:
            stmt = stmt.where(KnowledgeBase.id.in_(kb_ids))
        return list((await session.execute(stmt)).scalars().all())

    async def document_counts(
        self, session: AsyncSession, kb_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not kb_ids:
            return {}
        rows = (
            await session.execute(
                select(Document.kb_id, func.count(Document.id))
                .where(
                    Document.kb_id.in_(kb_ids),
                    Document.deleted_at.is_(None),
                )
                .group_by(Document.kb_id)
            )
        ).all()
        return {kb_id: count for kb_id, count in rows}
