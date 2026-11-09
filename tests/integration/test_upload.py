from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _file(name: str = "sample.md", content: bytes | None = None):
    raw = content if content is not None else (FIXTURES / name).read_bytes()
    return {"file": (name, raw, "text/markdown")}


async def _create_kb(client) -> str:
    response = await client.post("/api/v1/kbs", json={"name": "知识库"})
    return response.json()["id"]


async def test_upload_returns_202_and_pending(client_a):
    kb_id = await _create_kb(client_a)
    response = await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["duplicated"] is False


async def test_upload_writes_outbox_event(client_a, tenant_a):
    from app.core.db import session_scope

    kb_id = await _create_kb(client_a)
    await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())

    async with session_scope(tenant_id=tenant_a[0]) as session:
        count = (
            await session.execute(text("SELECT count(*) FROM outbox_events WHERE status='pending'"))
        ).scalar()
    assert count == 1


async def test_duplicate_upload_returns_same_document(client_a):
    kb_id = await _create_kb(client_a)
    first = await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())
    second = await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())

    assert second.status_code == 202
    assert second.json()["duplicated"] is True
    assert second.json()["document_id"] == first.json()["document_id"]


async def test_unsupported_type_rejected(client_a):
    kb_id = await _create_kb(client_a)
    response = await client_a.post(
        f"/api/v1/kbs/{kb_id}/documents",
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_TYPE"
    assert response.json()["error"]["message"] == "仅支持 PDF、Word、Markdown、TXT"


async def test_empty_file_rejected(client_a):
    kb_id = await _create_kb(client_a)
    response = await client_a.post(
        f"/api/v1/kbs/{kb_id}/documents", files={"file": ("a.md", b"", "text/markdown")}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EMPTY_FILE"


async def test_oversized_file_rejected(client_a, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "upload_max_bytes", 10)
    kb_id = await _create_kb(client_a)
    response = await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_viewer_cannot_upload(viewer_client_a, client_a):
    kb_id = await _create_kb(client_a)
    response = await viewer_client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())
    assert response.status_code == 403


async def test_list_and_get_document(client_a):
    kb_id = await _create_kb(client_a)
    uploaded = (await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())).json()

    listed = await client_a.get(f"/api/v1/kbs/{kb_id}/documents")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    detail = await client_a.get(f"/api/v1/documents/{uploaded['document_id']}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "sample.md"
    assert detail.json()["stage"] is None


async def test_delete_document_hides_it(client_a):
    kb_id = await _create_kb(client_a)
    uploaded = (await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())).json()

    assert (
        await client_a.delete(f"/api/v1/documents/{uploaded['document_id']}")
    ).status_code == 204
    assert (await client_a.get(f"/api/v1/documents/{uploaded['document_id']}")).status_code == 404
    assert (await client_a.get(f"/api/v1/kbs/{kb_id}/documents")).json()["total"] == 0


async def test_retry_does_not_duplicate_ingest_events(client_a, tenant_a):
    """重复点重试不应堆积多个入库事件：真实 embedding 下那是重复计费。"""
    from sqlalchemy import text

    from app.core.db import session_scope

    kb_id = await _create_kb(client_a)
    uploaded = (await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=_file())).json()
    doc_id = uploaded["document_id"]

    for _ in range(3):
        assert (await client_a.post(f"/api/v1/documents/{doc_id}/retry")).status_code == 200

    async with session_scope(tenant_id=tenant_a[0]) as session:
        pending = (
            await session.execute(
                text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE aggregate_id = :i AND event_type = 'doc.ingest' "
                    "AND status IN ('pending', 'processing')"
                ),
                {"i": doc_id},
            )
        ).scalar_one()

    assert pending == 1, f"重试产生了 {pending} 个待处理入库事件"
