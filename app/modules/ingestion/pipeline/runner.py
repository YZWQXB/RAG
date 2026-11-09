"""入库状态机。

pending → processing(parsing) → (chunking) → (embedding) → ready
失败：attempts 未达上限则回到 pending 并退避重试；达到上限则 failed 并给出中文原因。

每次阶段切换都提交事务，这样前端轮询能看到"解析中/切分中/向量化中"的真实进度，
而不是一个长时间不动的转圈。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.tenant import bind_tenant
from app.modules.ingestion.models import Document, DocumentVersion, IngestionJob
from app.modules.ingestion.pipeline.chunk import chunk_document
from app.modules.ingestion.pipeline.embed import embed_drafts, write_chunks
from app.modules.ingestion.pipeline.parse import parse_document
from app.modules.knowledge.models import KnowledgeBase
from app.providers import get_embedding_provider
from app.providers.storage import get_storage

log = get_logger("ingestion")

BACKOFF_SECONDS = [1, 4, 16]


def _user_message(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "文档处理超时，请检查文件是否过大或格式异常"
    if isinstance(exc, AppError):
        return exc.message
    return "处理文档时发生错误，请重试或联系管理员"


async def _commit_and_rebind(session, tenant_id: uuid.UUID) -> None:
    """提交后必须重新绑定租户。

    set_config(..., is_local=true) 的作用域是事务；commit() 结束事务后 GUC 会失效，
    下一个事务里 RLS 会过滤掉所有行，导致 UPDATE 匹配 0 行并抛 StaleDataError。
    这是"多阶段提交 + 事务级租户上下文"组合下最容易踩的坑。
    """
    await session.commit()
    await bind_tenant(session, tenant_id)


async def run_ingestion(
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    attempt: int = 0,
) -> None:
    async with SessionLocal() as session:
        await session.begin()
        await bind_tenant(session, tenant_id)

        document = await session.get(Document, document_id)
        version = await session.get(DocumentVersion, version_id)
        if document is None or version is None:
            log.warning("ingestion_missing_document", document_id=str(document_id))
            return

        job = (
            await session.execute(
                select(IngestionJob).where(
                    IngestionJob.document_id == document_id,
                    IngestionJob.version_id == version_id,
                )
            )
        ).scalar_one_or_none()
        if job is None:
            job = IngestionJob(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                document_id=document_id,
                version_id=version_id,
                stage="parsing",
                status="running",
            )
            session.add(job)

        job.status = "running"
        job.stage = "parsing"
        job.started_at = datetime.now(UTC)
        document.status = "processing"
        document.stage = "parsing"
        document.error = None
        await _commit_and_rebind(session, tenant_id)

        async def _pipeline() -> int:
            """解析 → 分块 → 向量化 → 落库。单独成函数是为了套一个统一的超时。"""
            storage = get_storage()
            raw = await storage.get(version.storage_key)

            parsed = parse_document(document.title, raw)

            document.stage = "chunking"
            job.stage = "chunking"
            await _commit_and_rebind(session, tenant_id)

            kb = await session.get(KnowledgeBase, document.kb_id)
            kb_config = dict(kb.chunk_config or {}) if kb else {}
            drafts = chunk_document(parsed, document.title, kb_config)

            document.stage = "embedding"
            job.stage = "embedding"
            await _commit_and_rebind(session, tenant_id)

            provider = get_embedding_provider()
            vectors = await embed_drafts(provider, drafts)
            await write_chunks(
                session,
                tenant_id=tenant_id,
                kb_id=document.kb_id,
                document_id=document_id,
                version_id=version_id,
                drafts=drafts,
                vectors=vectors,
            )

            document.status = "ready"
            document.stage = None
            document.error = None
            document.current_version_id = version_id
            document.updated_at = datetime.now(UTC)
            version.status = "ready"
            job.status = "done"
            job.finished_at = datetime.now(UTC)
            await _commit_and_rebind(session, tenant_id)
            return len(drafts)

        try:
            chunks = await asyncio.wait_for(_pipeline(), timeout=settings.ingest_timeout_seconds)
            log.info("ingestion_done", document_id=str(document_id), chunks=chunks)
        except TimeoutError as exc:
            # 单个病态文档不能无限占用唯一的轮询循环，超时按失败处理并交给退避重试
            log.error("ingestion_timeout", document_id=str(document_id))
            with contextlib.suppress(Exception):
                await session.rollback()
            await _mark_failure(
                tenant_id=tenant_id,
                document_id=document_id,
                version_id=version_id,
                attempt=attempt,
                exc=exc,
            )
            raise
        except Exception as exc:
            log.error("ingestion_error", document_id=str(document_id), detail=repr(exc))
            await session.rollback()
            await _mark_failure(
                tenant_id=tenant_id,
                document_id=document_id,
                version_id=version_id,
                attempt=attempt,
                exc=exc,
            )
            raise


async def _mark_failure(
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    attempt: int,
    exc: Exception,
) -> None:
    """失败必须用独立事务记录，否则回滚会把错误信息一起丢掉。"""
    exhausted = attempt + 1 >= settings.ingest_max_attempts
    async with SessionLocal() as session:
        await session.begin()
        await bind_tenant(session, tenant_id)

        document = await session.get(Document, document_id)
        job = (
            await session.execute(
                select(IngestionJob).where(
                    IngestionJob.document_id == document_id,
                    IngestionJob.version_id == version_id,
                )
            )
        ).scalar_one_or_none()

        if document is not None:
            document.status = "failed" if exhausted else "pending"
            document.stage = None
            document.error = _user_message(exc)
            document.updated_at = datetime.now(UTC)
        if job is not None:
            job.attempts = attempt + 1
            job.status = "failed" if exhausted else "pending"
            job.last_error = _user_message(exc)
            if exhausted:
                job.finished_at = datetime.now(UTC)
            else:
                delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        await session.commit()

    log.warning(
        "ingestion_failed",
        document_id=str(document_id),
        attempt=attempt + 1,
        exhausted=exhausted,
        error=type(exc).__name__,
    )
