"""配额与用量。

两类指标来源不同：
- documents / storage_bytes：查库为准，永远不降级。
- queries_per_month / llm_tokens_per_month：Redis 月计数器，Redis 不可用时放行（可用性优先），
  但每次用量都会异步落 usage_records，可用 rebuild_usage_counters 重建计数器。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import QuotaExceededError
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.modules.ingestion.models import Document
from app.modules.quota.models import UsageRecord
from app.modules.quota.plans import METRIC_LABELS, MONTHLY_METRICS, limits_for
from app.modules.tenancy.models import Tenant

log = get_logger("quota")


def _month_key(now: datetime | None = None) -> str:
    """按月归集计数器的键。

    必须用业务时区而不是 UTC：UTC 月初对应北京时间每月 1 日 08:00，
    用户会看到"月初还能超额用一会儿、月底提前 8 小时被卡"。
    """
    moment = now or datetime.now(UTC)
    return moment.astimezone(ZoneInfo(settings.quota_timezone)).strftime("%Y%m")


def _redis_key(tenant_id: uuid.UUID, metric: str, month: str) -> str:
    return f"quota:{tenant_id}:{metric}:{month}"


async def _plan_for(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    tenant = await session.get(Tenant, tenant_id)
    return tenant.plan if tenant else "free"


async def _db_usage(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[int, int]:
    row = (
        await session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.size_bytes), 0),
            ).where(Document.deleted_at.is_(None))
        )
    ).one()
    return int(row[0]), int(row[1])


async def _redis_usage(tenant_id: uuid.UUID, metric: str, month: str) -> int | None:
    try:
        value = await get_redis().get(_redis_key(tenant_id, metric, month))
        return int(value) if value else 0
    except Exception as exc:
        log.warning("quota_redis_unavailable", metric=metric, error=type(exc).__name__)
        return None


async def current_usage(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    plan = await _plan_for(session, tenant_id)
    limits = limits_for(plan)
    doc_count, storage = await _db_usage(session, tenant_id)
    month = _month_key()

    queries = await _redis_usage(tenant_id, "queries_per_month", month)
    tokens = await _redis_usage(tenant_id, "llm_tokens_per_month", month)

    return {
        "documents": doc_count,
        "storage_bytes": storage,
        "queries_per_month": queries if queries is not None else 0,
        "llm_tokens_per_month": tokens if tokens is not None else 0,
        "_limits": limits,  # type: ignore[dict-item]
    }


async def assert_quota(
    session: AsyncSession, tenant_id: uuid.UUID, metric: str, delta: int = 1
) -> None:
    plan = await _plan_for(session, tenant_id)
    limits = limits_for(plan)
    limit = limits.get(metric)
    if limit is None:
        return

    if metric == "documents":
        used, _ = await _db_usage(session, tenant_id)
    elif metric == "storage_bytes":
        _, used = await _db_usage(session, tenant_id)
    else:
        month_used = await _redis_usage(tenant_id, metric, _month_key())
        if month_used is None:
            return  # Redis 不可用：放行，避免把配额故障放大成服务不可用
        used = month_used

    if used + delta > limit:
        raise QuotaExceededError(
            METRIC_LABELS.get(metric, "额度已用尽"),
            details={
                "metric": metric,
                "limit": limit,
                "used": used,
                "remaining": max(0, limit - used),
            },
        )


async def record_usage(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    metric: str,
    quantity: int = 1,
    **meta,
) -> None:
    if quantity <= 0:
        return

    session.add(
        UsageRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            metric=metric,
            quantity=quantity,
            meta=meta,
        )
    )
    await session.flush()

    if metric not in MONTHLY_METRICS:
        return

    month = _month_key()
    try:
        redis = get_redis()
        key = _redis_key(tenant_id, metric, month)
        await redis.incrby(key, quantity)
        # 首次创建时设置过期到下月底，避免计数器永久驻留
        if await redis.ttl(key) < 0:
            await redis.expire(key, 40 * 24 * 3600)
    except Exception as exc:
        log.warning("usage_counter_failed", metric=metric, error=type(exc).__name__)


async def usage_report(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
    plan = await _plan_for(session, tenant_id)
    limits = limits_for(plan)
    usage = await current_usage(session, tenant_id)
    return {
        "plan": plan,
        "usage": {
            metric: {"used": value, "limit": limits.get(metric, 0)}
            for metric, value in usage.items()
            if not metric.startswith("_")
        },
    }
