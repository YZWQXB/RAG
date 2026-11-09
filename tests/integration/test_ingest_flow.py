from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _file():
    return {"file": ("sample.md", (FIXTURES / "sample.md").read_bytes(), "text/markdown")}


async def _upload(client) -> tuple[str, str]:
    kb_id = (await client.post("/api/v1/kbs", json={"name": "入库"})).json()["id"]
    doc_id = (await client.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())).json()[
        "document_id"
    ]
    return kb_id, doc_id


async def test_upload_to_ready(client_a, monkeypatch):
    from worker.tasks import outbox as outbox_module

    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])
    _, doc_id = await _upload(client_a)

    processed = await outbox_module.poll_once()
    assert processed >= 1

    detail = (await client_a.get(f"/api/v1/documents/{doc_id}")).json()
    assert detail["status"] == "ready"
    assert detail["stage"] is None
    assert detail["chunk_count"] == 3
    assert detail["page_count"] == 1


async def test_chunks_written_with_tenant_scope(client_a, tenant_a):
    from sqlalchemy import text

    from app.core.db import session_scope
    from worker.tasks import outbox as outbox_module

    _, doc_id = await _upload(client_a)
    await outbox_module.poll_once()

    async with session_scope(tenant_id=tenant_a[0]) as session:
        rows = (
            await session.execute(text("SELECT tenant_id, count(*) FROM chunks GROUP BY tenant_id"))
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == tenant_a[0]
    assert rows[0][1] == 3


async def test_invalid_document_fails_after_retries(client_a, tenant_a, monkeypatch):
    from app.modules.ingestion.pipeline import runner
    from app.providers import set_embedding_provider
    from worker.tasks import outbox as outbox_module

    class BrokenEmbedding:
        async def embed(self, texts):  # noqa: ANN001
            raise RuntimeError("boom")

    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])
    monkeypatch.setattr(runner, "BACKOFF_SECONDS", [0, 0, 0])
    set_embedding_provider(BrokenEmbedding())

    _, doc_id = await _upload(client_a)

    for _ in range(3):
        await outbox_module.poll_once()

    detail = (await client_a.get(f"/api/v1/documents/{doc_id}")).json()
    assert detail["status"] == "failed"
    assert detail["error"]


async def test_retry_recovers_failed_document(client_a, monkeypatch):
    from app.modules.ingestion.pipeline import runner
    from app.providers import set_embedding_provider
    from app.providers.embedding.fake import FakeEmbedding
    from worker.tasks import outbox as outbox_module

    class BrokenEmbedding:
        async def embed(self, texts):  # noqa: ANN001
            raise RuntimeError("boom")

    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])
    monkeypatch.setattr(runner, "BACKOFF_SECONDS", [0, 0, 0])
    set_embedding_provider(BrokenEmbedding())

    _, doc_id = await _upload(client_a)
    for _ in range(3):
        await outbox_module.poll_once()
    assert (await client_a.get(f"/api/v1/documents/{doc_id}")).json()["status"] == "failed"

    set_embedding_provider(FakeEmbedding())
    retry = await client_a.post(f"/api/v1/documents/{doc_id}/retry")
    assert retry.status_code == 200
    await outbox_module.poll_once()

    detail = (await client_a.get(f"/api/v1/documents/{doc_id}")).json()
    assert detail["status"] == "ready"
    assert detail["chunk_count"] == 3


async def test_worker_is_idempotent_on_rerun(client_a, tenant_a, monkeypatch):
    """同一事件重复处理不应产生重复块（upsert + 尾部清理）。"""
    from sqlalchemy import text

    from app.core.db import admin_engine
    from app.modules.ingestion.pipeline.runner import run_ingestion
    from worker.tasks import outbox as outbox_module

    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])
    _, doc_id = await _upload(client_a)
    await outbox_module.poll_once()

    async with admin_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT tenant_id, current_version_id FROM documents WHERE id = :i"),
                    {"i": doc_id},
                )
            )
            .mappings()
            .one()
        )

    await run_ingestion(
        tenant_id=row["tenant_id"],
        document_id=doc_id,
        version_id=row["current_version_id"],
    )

    async with admin_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM chunks WHERE document_id = :i"), {"i": doc_id}
            )
        ).scalar()
    assert count == 3


def _make_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Employee Handbook", fontsize=20)
    page.insert_text((72, 120), "Annual leave is five days per year.", fontsize=11)
    page2 = doc.new_page()
    page2.insert_text((72, 80), "Reimbursement must be submitted in 30 days.", fontsize=11)
    raw = doc.tobytes()
    doc.close()
    return raw


async def test_pdf_upload_to_ready_with_pages(client_a, monkeypatch):
    """PDF 链路：验证页码被正确带到 chunk 上，而不只是文本能入库。"""
    from worker.tasks import outbox as outbox_module

    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])
    kb_id = (await client_a.post("/api/v1/kbs", json={"name": "PDF"})).json()["id"]
    uploaded = await client_a.post(
        f"/api/v1/kbs/{kb_id}/documents",
        files={"file": ("handbook.pdf", _make_pdf(), "application/pdf")},
    )
    assert uploaded.status_code == 202

    await outbox_module.poll_once()

    detail = (await client_a.get(f"/api/v1/documents/{uploaded.json()['document_id']}")).json()
    assert detail["status"] == "ready"
    assert detail["chunk_count"] >= 1
    assert detail["page_count"] == 2


async def test_large_document_is_chunked_within_limits(client_a, tenant_a, monkeypatch):
    """大文档：验证分块不会超长、不会丢内容、状态能收敛到 ready。"""
    from sqlalchemy import func, select

    from app.core.db import session_scope
    from app.modules.ingestion.models import Chunk
    from app.modules.ingestion.pipeline.chunk import DEFAULT_CONFIG
    from worker.tasks import outbox as outbox_module

    monkeypatch.setattr(outbox_module, "BACKOFF_SECONDS", [0, 0, 0])

    sections = []
    for i in range(2000):
        sections.append(f"## 第 {i} 条")
        sections.append("")
        sections.append(
            f"第 {i} 条规定的办理时限为 {i % 30 + 1} 个工作日，责任岗位为第 {i % 5} 类。"
        )
        sections.append("")
    body = "# 大型制度汇编\n\n" + "\n".join(sections)
    raw = body.encode("utf-8")
    assert len(raw) > 150_000

    kb_id = (await client_a.post("/api/v1/kbs", json={"name": "大文档"})).json()["id"]
    uploaded = await client_a.post(
        f"/api/v1/kbs/{kb_id}/documents",
        files={"file": ("huge.md", raw, "text/markdown")},
    )
    assert uploaded.status_code == 202

    await outbox_module.poll_once()

    doc_id = uploaded.json()["document_id"]
    detail = (await client_a.get(f"/api/v1/documents/{doc_id}")).json()
    assert detail["status"] == "ready"
    assert detail["chunk_count"] > 100

    async with session_scope(tenant_id=tenant_a[0]) as session:
        longest = (await session.execute(select(func.max(func.length(Chunk.content))))).scalar_one()
        covered = (
            await session.execute(
                select(func.count(Chunk.id)).where(
                    Chunk.content.contains("第 1999 条规定的办理时限")
                )
            )
        ).scalar_one()

    assert longest <= DEFAULT_CONFIG["max_chars"] + 120, "块长度超出上限"
    assert covered >= 1, "文末内容没有被收录"
