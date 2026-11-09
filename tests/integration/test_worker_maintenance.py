"""worker 维护任务：卡死任务自愈与用量计数器重建。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import admin_session
from worker.tasks.aggregate import rebuild_usage_counters, rescan_stuck

pytestmark = pytest.mark.db


async def _seed_document(tenant_id, user_id, *, status: str, age_minutes: int) -> uuid.UUID:
    """借用 seed_document 建好 KB 与文档，再把状态与时间改成需要的样本。"""
    from tests.helpers import basis, seed_document

    _, doc_id, _ = await seed_document(tenant_id, user_id, [("占位内容", basis(0))])
    stale = datetime.now(UTC) - timedelta(minutes=age_minutes)
    async with admin_session() as session:
        await session.execute(
            text("UPDATE documents SET status = :s, updated_at = :ts WHERE id = :i"),
            {"s": status, "ts": stale, "i": doc_id},
        )
    return doc_id


async def test_rescan_requeues_stuck_documents(tenant_a):
    tenant_id, user_id, _ = tenant_a
    doc_id = await _seed_document(tenant_id, user_id, status="processing", age_minutes=20)

    assert await rescan_stuck() == 1

    async with admin_session() as session:
        status = (
            await session.execute(text("SELECT status FROM documents WHERE id = :i"), {"i": doc_id})
        ).scalar_one()
        events = (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE aggregate_id = :i AND event_type = 'doc.ingest'"
                ),
                {"i": doc_id},
            )
        ).scalar_one()

    assert status == "pending"
    assert events == 1


async def test_rescan_ignores_recent_processing(tenant_a):
    tenant_id, user_id, _ = tenant_a
    await _seed_document(tenant_id, user_id, status="processing", age_minutes=1)
    assert await rescan_stuck() == 0


async def test_rescan_ignores_ready_documents(tenant_a):
    tenant_id, user_id, _ = tenant_a
    await _seed_document(tenant_id, user_id, status="ready", age_minutes=100)
    assert await rescan_stuck() == 0


async def test_rebuild_usage_counters_restores_redis(tenant_a):
    from app.core.redis_client import get_redis

    tenant_id = tenant_a[0]
    month = datetime.now(UTC).strftime("%Y%m")
    key = f"quota:{tenant_id}:queries_per_month:{month}"

    async with admin_session() as session:
        for qty in (3, 4, 5):
            await session.execute(
                text(
                    "INSERT INTO usage_records (id, tenant_id, metric, quantity, occurred_at) "
                    "VALUES (:id, :tid, 'queries_per_month', :q, now())"
                ),
                {"id": uuid.uuid4(), "tid": tenant_id, "q": qty},
            )

    await get_redis().delete(key)
    await rebuild_usage_counters()

    assert int(await get_redis().get(key)) == 12
