"""outbox 轮询与事件分发。

跨租户调度必须用 admin_engine：outbox_events 不含 tenant_id，且需要看到所有租户的
待处理事件。这是全项目唯一允许绕过 RLS 的调度路径——拿到 tenant_id 后立刻切回租户会话
处理业务数据，不在这里做任何业务读写。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, text

from app.core.config import settings
from app.core.db import admin_engine
from app.core.logging import get_logger
from app.modules.ingestion.pipeline.runner import run_ingestion
from app.providers.storage import get_storage

log = get_logger("outbox")

BACKOFF_SECONDS = [1, 4, 16]


async def _claim(limit: int) -> list[dict]:
    async with admin_engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT id, aggregate_type, aggregate_id, event_type, payload, attempts "
                "FROM outbox_events "
                "WHERE status = 'pending' AND available_at <= now() "
                "ORDER BY created_at LIMIT :lim FOR UPDATE SKIP LOCKED"
            ),
            {"lim": limit},
        )
        rows = [dict(r) for r in result.mappings().all()]
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        stmt = text("UPDATE outbox_events SET status = 'processing' WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        await conn.execute(stmt, {"ids": ids})
    return rows


async def _tenant_for_document(document_id: uuid.UUID) -> uuid.UUID | None:
    async with admin_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT tenant_id FROM documents WHERE id = :id"), {"id": document_id}
        )
        row = result.scalar_one_or_none()
    return row


async def _finish(event_id: uuid.UUID, status: str) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE outbox_events SET status = :s WHERE id = :i"),
            {"s": status, "i": event_id},
        )


async def _reschedule(event_id: uuid.UUID, attempts: int) -> None:
    exhausted = attempts + 1 >= settings.ingest_max_attempts
    delay = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS) - 1)]
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE outbox_events SET status = :st, attempts = :a, available_at = :at "
                "WHERE id = :i"
            ),
            {
                "st": "failed" if exhausted else "pending",
                "a": attempts + 1,
                "at": datetime.now(UTC) + timedelta(seconds=delay),
                "i": event_id,
            },
        )


async def _dispatch(row: dict) -> None:
    event_id = row["id"]
    attempts = row["attempts"] or 0
    aggregate_id = row["aggregate_id"]

    tenant_id = await _tenant_for_document(aggregate_id)
    if tenant_id is None:
        await _finish(event_id, "failed")
        return

    try:
        if row["event_type"] == "doc.ingest":
            version_id = uuid.UUID(row["payload"]["version_id"])
            await run_ingestion(
                tenant_id=tenant_id,
                document_id=aggregate_id,
                version_id=version_id,
                attempt=attempts,
            )
            await _finish(event_id, "done")
        elif row["event_type"] == "doc.delete":
            storage_key = row["payload"].get("storage_key")
            if storage_key:
                await get_storage().delete(storage_key)
            await _finish(event_id, "done")
        else:
            log.warning("unknown_event", event_type=row["event_type"])
            await _finish(event_id, "failed")
    except Exception as exc:
        # run_ingestion 已在独立事务里记录了失败原因，这里只负责调度重试
        await _reschedule(event_id, attempts)
        log.warning(
            "dispatch_failed",
            event_id=str(event_id),
            attempts=attempts + 1,
            error=type(exc).__name__,
        )


async def poll_once(limit: int = 10) -> int:
    rows = await _claim(limit)
    for row in rows:
        await _dispatch(row)
    return len(rows)
