"""基于 Redis 的固定窗口限流。

放行策略是"宁可少拦，不可错杀"：Redis 不可用时直接放行。
限流是用来抬高滥用成本的，不能因为缓存故障把正常用户挡在门外。
"""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.core.redis_client import get_redis

log = get_logger("ratelimit")

WINDOW_SECONDS = 60


async def hit(bucket: str, limit: int, window_seconds: int = WINDOW_SECONDS) -> tuple[bool, int]:
    """记一次命中，返回 (是否放行, 建议重试秒数)。"""
    if limit <= 0:
        return True, 0

    now = int(time.time())
    slot = now // window_seconds
    key = f"rl:{bucket}:{slot}"

    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            # 多留一个窗口，避免临界点上计数被过早清掉
            await redis.expire(key, window_seconds * 2)
    except Exception as exc:
        log.warning("ratelimit_degraded", bucket=bucket, error=type(exc).__name__)
        return True, 0

    if count > limit:
        retry_after = window_seconds - (now % window_seconds)
        return False, max(1, retry_after)
    return True, 0


def client_ip(request) -> str:  # noqa: ANN001
    """取客户端 IP。

    不直接信任 X-Forwarded-For：只有部署在可信反代后面时才成立，
    而"是否可信"由部署方决定，代码里默认用直连地址，避免被伪造头绕过限流。
    """
    if request is None or request.client is None:
        return "unknown"
    return request.client.host or "unknown"
