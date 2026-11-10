"""进程级共享 httpx 客户端。

每次问答都新建 AsyncClient 会重复做 TLS 握手（本机实测单次 200~600ms），
并发时各自占用连接。这里复用同一个客户端，靠 keep-alive 摊薄成本。

客户端归属进程，任何单次请求都不得关闭它——否则会掐断其他在途请求的连接。
进程退出时由 lifespan / worker 统一 close。

超时策略：默认 read 给到 120 秒（推理模型会先吐思维链，短超时会误杀），
调用方可按需覆盖更短的超时（如 embeddings 30 秒）。
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

log = get_logger("http")

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

# 连接数按「单进程 + 数十并发问答」估算：50 上限、20 保活足够，
# 再大会把上游限流压力放大成 429。
LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, limits=LIMITS)
        log.info("http_client_created")
    return _client


async def close_http_client() -> None:
    global _client
    client = _client
    _client = None
    if client is not None and not client.is_closed:
        await client.aclose()
        log.info("http_client_closed")
