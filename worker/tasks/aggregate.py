"""定时维护任务：卡住任务自愈 + 用量计数器重建。"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.core.db import admin_engine
from app.core.logging import get_logger

log = get_logger("worker.tasks")

STUCK_MINUTES = 10


async def rescan_stuck() -> int:
    """把长时间停留在 processing 的文档重新排队。

    进程被杀或容器重启会让文档卡在 processing，这条任务负责自愈。
    """
    async with admin_engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, tenant_id, current_version_id FROM documents "
                        "WHERE status = 'processing' "
                        "AND updated_at < now() - make_interval(mins => :mins)"
                    ),
                    {"mins": STUCK_MINUTES},
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            await conn.execute(
                text(
                    "UPDATE documents SET status='pending', stage=NULL, updated_at=now() "
                    "WHERE id = :id"
                ),
                {"id": row["id"]},
            )
            version_id = row["current_version_id"] or uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, aggregate_type, aggregate_id, event_type, payload, status) "
                    "VALUES (:id, 'document', :agg, 'doc.ingest', :payload, 'pending')"
                ),
                {
                    "id": uuid.uuid4(),
                    "agg": row["id"],
                    "payload": f'{{"version_id": "{version_id}"}}',
                },
            )

    if rows:
        log.info("rescan_stuck", count=len(rows))
    return len(rows)


async def rebuild_usage_counters() -> int:
    """按 usage_records 重算当月 Redis 计数器。

    计数器本身是实时 INCR 的，这里只做灾备重建：Redis 丢数据后调用一次即可恢复。
    """
    from datetime import UTC, datetime

    from sqlalchemy import text as sql_text

    from app.core.redis_client import get_redis

    now = datetime.now(UTC)
    month = now.strftime("%Y%m")
    async with admin_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    sql_text(
                        "SELECT tenant_id, metric, coalesce(sum(quantity), 0) AS total "
                        "FROM usage_records "
                        "WHERE occurred_at >= date_trunc('month', now()) "
                        "GROUP BY tenant_id, metric"
                    )
                )
            )
            .mappings()
            .all()
        )

    redis = get_redis()
    rebuilt = 0
    for row in rows:
        key = f"quota:{row['tenant_id']}:{row['metric']}:{month}"
        await redis.set(key, int(row["total"]), ex=40 * 24 * 3600)
        rebuilt += 1
    log.info("usage_counters_rebuilt", keys=rebuilt)
    return rebuilt
