"""单文档处理超时。

worker 只有一条轮询循环，一个病态文档（畸形 PDF、超大表格）如果无限跑，
会把全租户的入库一起堵死。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _file():
    return {"file": ("sample.md", (FIXTURES / "sample.md").read_bytes(), "text/markdown")}


async def test_slow_document_times_out_and_lands_failed(client_a, monkeypatch):
    from app.core.config import settings
    from app.modules.ingestion.pipeline import runner
    from app.providers import set_embedding_provider
    from worker.tasks import outbox as outbox_module

    class HangingEmbedding:
        async def embed(self, texts):  # noqa: ANN001
            await asyncio.sleep(30)

    monkeypatch.setattr(settings, "ingest_timeout_seconds", 0.2)
    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])
    monkeypatch.setattr(runner, "BACKOFF_SECONDS", [0, 0, 0])
    set_embedding_provider(HangingEmbedding())

    kb_id = (await client_a.post("/api/v1/kbs", json={"name": "超时"})).json()["id"]
    doc_id = (await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())).json()[
        "document_id"
    ]

    for _ in range(3):
        await outbox_module.poll_once()

    detail = (await client_a.get(f"/api/v1/documents/{doc_id}")).json()
    assert detail["status"] == "failed"
    assert "超时" in (detail["error"] or "")
