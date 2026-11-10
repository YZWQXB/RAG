"""入库并发闸门。

bge-m3 在进程内用 torch 的 16 个线程跑推理；10 个文档同时入库会互相抢核，
结果是每个都慢、整体更慢。闸门的作用是「排队」而不是「一起挤」。
"""

from __future__ import annotations

import asyncio
import uuid

import worker.tasks.outbox as outbox


def _row() -> dict:
    return {
        "id": uuid.uuid4(),
        "aggregate_type": "document",
        "aggregate_id": uuid.uuid4(),
        "event_type": "doc.ingest",
        "payload": {},
        "attempts": 0,
    }


async def test_poll_once_caps_concurrent_dispatches(monkeypatch):
    rows = [_row() for _ in range(6)]
    active = 0
    peak = 0

    async def fake_claim(limit: int):  # noqa: ANN001
        return rows

    async def fake_dispatch(row: dict):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(outbox, "_claim", fake_claim)
    monkeypatch.setattr(outbox, "_dispatch", fake_dispatch)
    monkeypatch.setattr(outbox.settings, "ingest_max_concurrent", 2)

    count = await outbox.poll_once()

    assert count == len(rows), "全部事件都应被处理"
    assert peak <= 2, f"并发上限应被闸门限制在 2，实际 {peak}"


async def test_poll_once_still_serial_when_limit_is_one(monkeypatch):
    rows = [_row() for _ in range(4)]
    active = 0
    peak = 0

    async def fake_claim(limit: int):  # noqa: ANN001
        return rows

    async def fake_dispatch(row: dict):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1

    monkeypatch.setattr(outbox, "_claim", fake_claim)
    monkeypatch.setattr(outbox, "_dispatch", fake_dispatch)
    monkeypatch.setattr(outbox.settings, "ingest_max_concurrent", 1)

    await outbox.poll_once()
    assert peak == 1


async def test_poll_once_tolerates_dispatch_failure(monkeypatch):
    """单个事件失败不能拖垮整批。"""

    rows = [_row() for _ in range(3)]
    seen: list[int] = []

    async def fake_claim(limit: int):  # noqa: ANN001
        return rows

    async def fake_dispatch(row: dict):
        seen.append(1)
        if len(seen) == 2:
            raise RuntimeError("boom")

    monkeypatch.setattr(outbox, "_claim", fake_claim)
    monkeypatch.setattr(outbox, "_dispatch", fake_dispatch)
    monkeypatch.setattr(outbox.settings, "ingest_max_concurrent", 2)

    count = await outbox.poll_once()
    assert count == 3
    assert len(seen) == 3, "失败后其余事件仍应被执行"
