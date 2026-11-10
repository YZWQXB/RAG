"""Prometheus 指标。

只暴露聚合量，**不带 tenant_id / user_id 标签**：
指标端点通常对监控系统开放，按租户打标签会把客户规模与活跃度泄露出去，
也会让时间序列基数失控。

路径统一做 UUID/数字归一化（`/documents/<uuid>` → `/documents/:id`），
否则每个文档都会生成一条时间序列。
"""

from __future__ import annotations

import re

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import text

from app.core.db import admin_engine
from app.core.logging import get_logger

log = get_logger("metrics")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

REQUEST_COUNT = Counter(
    "rag_http_requests_total",
    "HTTP 请求数",
    ["method", "route", "status"],
)

REQUEST_LATENCY = Histogram(
    "rag_http_request_duration_seconds",
    "HTTP 请求耗时",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 60.0),
)

CHAT_OUTCOME = Counter(
    "rag_chat_answers_total",
    "问答结果分布",
    ["outcome"],  # normal / no_answer / incomplete / error
)

OUTBOX_PENDING = Gauge("rag_outbox_pending", "待处理的 outbox 事件数")
DOCUMENTS_PROCESSING = Gauge("rag_documents_processing", "正在解析的文档数")


def route_label(path: str) -> str:
    return _UUID.sub(":id", path or "/")


def observe_request(method: str, path: str, status: int, seconds: float) -> None:
    route = route_label(path)
    REQUEST_COUNT.labels(method=method or "?", route=route, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method or "?", route=route).observe(seconds)


async def refresh_gauges() -> None:
    """抓取时刷新需要查库的指标。

    查询失败不能影响指标端点本身——监控抓不到数据比数据旧更糟。
    """
    try:
        async with admin_engine.connect() as conn:
            pending = (
                await conn.execute(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'pending'")
                )
            ).scalar_one()
            processing = (
                await conn.execute(
                    text("SELECT count(*) FROM documents WHERE status = 'processing'")
                )
            ).scalar_one()
        OUTBOX_PENDING.set(pending)
        DOCUMENTS_PROCESSING.set(processing)
    except Exception as exc:
        log.warning("metrics_gauge_refresh_failed", error=type(exc).__name__)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
