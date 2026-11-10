"""worker 进程入口。

两个角色：
- outbox 轮询器（每 1 秒）：入库与文件清理的唯一触发入口
- 定时维护（每 60 秒）：卡住任务自愈、用量计数器重建

刻意不引入 ARQ：当前只有一个队列语义（outbox 表），再叠一层 Redis 队列只会增加
运维面而没有收益。若将来需要复杂的定时编排，再替换本文件的调度部分即可。
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path

from app.core.config import settings
from app.core.db import dispose_engines
from app.core.http import close_http_client
from app.core.logging import configure_logging, get_logger
from app.core.mode import sync_from_env_if_changed
from app.core.redis_client import close_redis
from worker.tasks.aggregate import rebuild_usage_counters, rescan_stuck
from worker.tasks.outbox import poll_once

log = get_logger("worker")

POLL_INTERVAL = 1.0
MAINTENANCE_INTERVAL = 60.0
READY_FLAG = Path("/tmp/worker.ok")


async def _poll_loop(stop: asyncio.Event) -> None:
    log.info("poller_started")
    while not stop.is_set():
        # API 切换离线/在线时只会改它自己的内存和 .env；本进程靠 mtime 跟随，
        # 否则会出现「API 已离线、worker 仍用真实模型向量化」的跨空间入库。
        sync_from_env_if_changed()
        try:
            processed = await poll_once()
            if processed:
                log.info("poll_batch", count=processed)
        except Exception as exc:
            log.warning("poll_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)


async def _maintenance_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=MAINTENANCE_INTERVAL)
        if stop.is_set():
            break
        with contextlib.suppress(Exception):
            await rescan_stuck()
        if stop.is_set():
            break
        with contextlib.suppress(Exception):
            await rebuild_usage_counters()


async def main() -> None:
    configure_logging(json_output=settings.is_production)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    with contextlib.suppress(OSError):
        READY_FLAG.write_text("ok")

    log.info("worker_started", env=settings.env)
    tasks = [
        asyncio.create_task(_poll_loop(stop)),
        asyncio.create_task(_maintenance_loop(stop)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        await close_http_client()
        await close_redis()
        await dispose_engines()
        log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
