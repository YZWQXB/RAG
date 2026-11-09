"""存活探针与就绪探针必须分离。

混在一起的后果：Redis 抖动会让编排系统判定进程已死并重启，
把一次缓存故障放大成整批实例重启。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


@pytest.fixture
def redis_down(monkeypatch):
    async def _down() -> bool:
        return False

    monkeypatch.setattr("app.core.redis_client.ping_redis", _down)


async def test_healthz_stays_200_when_redis_is_down(anon_client, redis_down):
    response = await anon_client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


async def test_readyz_reports_degraded_when_redis_is_down(anon_client, redis_down):
    response = await anon_client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] != "ok"


async def test_readyz_ok_when_all_dependencies_up(anon_client):
    response = await anon_client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "redis": "ok"}
