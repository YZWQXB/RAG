from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.core.security import Principal
from app.modules.audit import service as audit
from app.modules.iam.permissions import Action, require_kb_permission
from app.modules.ingestion.models import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    OutboxEvent,
)
from app.modules.ingestion.schemas import ChunkSourceOut, DocumentOut, UploadOut
from app.modules.quota.service import assert_quota, record_usage
from app.providers.storage import get_storage
from app.providers.storage.base import doc_key

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


def _suffix(filename: str) -> str:
    return PurePosixPath(filename or "").suffix.lower()


def _validate(filename: str, raw: bytes) -> str:
    suffix = _suffix(filename)
    if suffix not in ALLOWED_TYPES:
        raise BadRequestError("仅支持 PDF、Word、Markdown、TXT", code="UNSUPPORTED_TYPE")
    if not raw:
        raise BadRequestError("文件内容为空", code="EMPTY_FILE")
    if len(raw) > settings.upload_max_bytes:
        raise BadRequestError("单个文件不能超过 50MB", code="FILE_TOO_LARGE")
    return ALLOWED_TYPES[suffix]


async def upload_document(
    session: AsyncSession,
    principal: Principal,
    kb_id: uuid.UUID,
    filename: str,
    raw: bytes,
) -> UploadOut:
    await require_kb_permission(session, principal, kb_id, Action.WRITE)
    await assert_quota(session, principal.tenant_id, "documents", 1)
    await assert_quota(session, principal.tenant_id, "storage_bytes", len(raw))

    mime = _validate(filename, raw)
    digest = hashlib.sha256(raw).hexdigest()

    existing = (
        await session.execute(
            select(Document).where(
                Document.kb_id == kb_id,
                Document.content_hash == digest,
                Document.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return UploadOut(
            document_id=existing.id,
            status=existing.status,
            stage=existing.stage,
            duplicated=True,
        )

    document_id = uuid.uuid4()
    version_id = uuid.uuid4()
    title = (filename or "未命名文档")[:255]
    key = doc_key(principal.tenant_id, kb_id, document_id, 1, filename)

    document = Document(
        id=document_id,
        tenant_id=principal.tenant_id,
        kb_id=kb_id,
        title=title,
        mime=mime,
        size_bytes=len(raw),
        content_hash=digest,
        status="pending",
        created_by=principal.user_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    version = DocumentVersion(
        id=version_id,
        tenant_id=principal.tenant_id,
        document_id=document_id,
        version_no=1,
        storage_key=key,
        content_hash=digest,
        status="pending",
    )
    job = IngestionJob(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        document_id=document_id,
        version_id=version_id,
        stage="parsing",
        status="pending",
    )
    event = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="document",
        aggregate_id=document_id,
        event_type="doc.ingest",
        payload={"version_id": str(version_id)},
        status="pending",
    )

    session.add_all([document, version, job, event])
    await session.flush()

    # 先写对象存储，再让请求事务提交：
    # 若上传失败会整体回滚，不留下"永卡 pending"的文档记录；代价是极小概率产生孤儿文件，
    # 由 doc.delete / 运维清理脚本处理。
    await get_storage().put(key, raw, mime)
    await record_usage(session, principal.tenant_id, "doc_ingested", 1)

    return UploadOut(document_id=document_id, status="pending", stage=None, duplicated=False)


async def _counts(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Chunk.document_id,
                func.count(Chunk.id),
                func.coalesce(func.max(Chunk.page_to), 0),
            )
            .where(Chunk.document_id.in_(document_ids))
            .group_by(Chunk.document_id)
        )
    ).all()
    return {doc_id: (count, pages) for doc_id, count, pages in rows}


def _to_out(document: Document, count: int, pages: int) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        kb_id=document.kb_id,
        title=document.title,
        status=document.status,
        stage=document.stage,
        error=document.error,
        chunk_count=count,
        page_count=pages,
        size_bytes=document.size_bytes,
        created_at=document.created_at,
    )


async def get_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID
) -> DocumentOut:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError()
    await require_kb_permission(session, principal, document.kb_id, Action.READ)
    counts = await _counts(session, [document_id])
    count, pages = counts.get(document_id, (0, 0))
    return _to_out(document, count, pages)


async def list_documents(
    session: AsyncSession,
    principal: Principal,
    kb_id: uuid.UUID,
    *,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[DocumentOut], int]:
    await require_kb_permission(session, principal, kb_id, Action.READ)

    conditions = [Document.kb_id == kb_id, Document.deleted_at.is_(None)]
    if status:
        conditions.append(Document.status == status)

    total = (await session.execute(select(func.count(Document.id)).where(*conditions))).scalar_one()

    documents = (
        (
            await session.execute(
                select(Document)
                .where(*conditions)
                .order_by(Document.created_at.desc())
                .offset(max(0, (page - 1) * size))
                .limit(size)
            )
        )
        .scalars()
        .all()
    )

    counts = await _counts(session, [d.id for d in documents])
    items = [_to_out(d, *counts.get(d.id, (0, 0))) for d in documents]
    return items, total


async def retry_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID
) -> UploadOut:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError()
    await require_kb_permission(session, principal, document.kb_id, Action.WRITE)

    if document.status not in {"failed", "pending"}:
        raise BadRequestError("该文档当前状态无需重试", code="NOT_RETRYABLE")

    versions = (
        (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    if not versions:
        raise NotFoundError()
    version = versions[0]

    document.status = "pending"
    document.stage = None
    document.error = None
    document.updated_at = datetime.now(UTC)

    job = (
        await session.execute(
            select(IngestionJob).where(
                IngestionJob.document_id == document_id,
                IngestionJob.version_id == version.id,
            )
        )
    ).scalar_one_or_none()
    if job is not None:
        job.attempts = 0
        job.status = "pending"
        job.last_error = None

    # 已经有排队中/处理中的入库事件时不再重复投递：
    # 重复事件会让同一文档被并行重新处理，接真实 embedding 就是重复计费。
    in_flight = (
        await session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == document_id,
                OutboxEvent.event_type == "doc.ingest",
                OutboxEvent.status.in_(("pending", "processing")),
            )
        )
    ).first()

    if in_flight is None:
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                aggregate_type="document",
                aggregate_id=document_id,
                event_type="doc.ingest",
                payload={"version_id": str(version.id)},
                status="pending",
            )
        )
    await session.flush()
    return UploadOut(document_id=document_id, status="pending", stage=None)


async def delete_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID
) -> None:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError()
    await require_kb_permission(session, principal, document.kb_id, Action.WRITE)

    document.deleted_at = datetime.now(UTC)
    document.updated_at = datetime.now(UTC)
    await audit.record(
        session,
        principal,
        "document.delete",
        target_type="document",
        target_id=document_id,
        detail={"title": document.title, "kb_id": str(document.kb_id)},
    )
    # 立即删块，保证检索不会再召回；文件清理交给 worker 异步处理
    await session.execute(
        delete(Chunk)
        .where(Chunk.document_id == document_id)
        .execution_options(synchronize_session=False)
    )

    versions = (
        (
            await session.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            )
        )
        .scalars()
        .all()
    )
    for version in versions:
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                aggregate_type="document",
                aggregate_id=document_id,
                event_type="doc.delete",
                payload={"storage_key": version.storage_key},
                status="pending",
            )
        )
    await session.flush()


async def get_chunk_source(
    session: AsyncSession, principal: Principal, chunk_id: uuid.UUID
) -> ChunkSourceOut:
    """取分块原文，供前端点击引用时定位。

    先靠 RLS 挡跨租户（查不到即 404，不泄露存在性），再按 kb 权限挡同租户越权。
    """
    chunk = await session.get(Chunk, chunk_id)
    if chunk is None:
        raise NotFoundError()

    await require_kb_permission(session, principal, chunk.kb_id, Action.READ)

    document = await session.get(Document, chunk.document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError()

    return ChunkSourceOut(
        id=chunk.id,
        document_id=chunk.document_id,
        kb_id=chunk.kb_id,
        title=document.title,
        chunk_index=chunk.chunk_index,
        heading_path=chunk.heading_path,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        content=chunk.content,
    )
