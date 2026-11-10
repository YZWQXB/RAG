"""指标端点的最小验证：能抓、有数据、标签基数可控。"""

from __future__ import annotations

from app.core.metrics import route_label


def test_route_label_normalizes_uuid():
    assert route_label("/api/v1/chunks/0f0e1d2c-1111-2222-3333-444455556666") == (
        "/api/v1/chunks/:id"
    )


def test_route_label_keeps_static_path():
    assert route_label("/api/v1/conversations") == "/api/v1/conversations"


async def test_metrics_endpoint_exposes_counters(client_a):
    # 先制造一次请求，确保计数器非零
    await client_a.get("/api/v1/kbs")

    response = await client_a.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "rag_http_requests_total" in body
    assert "rag_http_request_duration_seconds" in body
    assert "rag_outbox_pending" in body


async def test_metrics_can_be_disabled(client_a, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "metrics_enabled", False)
    response = await client_a.get("/metrics")
    assert response.status_code == 404
