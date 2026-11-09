"""查询向量缓存。

只缓存向量，不缓存问题原文——Redis 里不应留存用户提问文本。
Redis 不可用时降级为直接调用，不阻断检索。
"""

from __future__ import annotations

import hashlib
import json

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.providers import get_embedding_provider

log = get_logger("embedding_cache")

TTL_SECONDS = 86400


def _key(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # 必须包含 provider 实现类型：fake 与 api 的输出空间完全不同，
    # 只带模型名会让两种实现的缓存互相污染。
    provider = type(get_embedding_provider()).__name__
    return f"emb:{provider}:{settings.embedding_model}:{digest}"


async def embed_cached(text: str) -> list[float]:
    key = _key(text)
    try:
        cached = await get_redis().get(key)
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log.warning("embedding_cache_miss_degraded", error=type(exc).__name__)

    vectors = await get_embedding_provider().embed([text])
    vector = vectors[0]

    try:
        await get_redis().setex(key, TTL_SECONDS, json.dumps(vector))
    except Exception as exc:
        log.warning("embedding_cache_write_failed", error=type(exc).__name__)
    return vector
