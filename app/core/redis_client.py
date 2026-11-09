"""Redis 客户端。所有 Redis 访问都经过这里，便于降级处理。"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("redis")

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def ping_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception as exc:
        log.warning("redis_unavailable", error=type(exc).__name__)
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
