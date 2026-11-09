from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.modules.quota.service import assert_quota, record_usage, usage_report
from tests.helpers import basis, seed_document

pytestmark = pytest.mark.db


async def test_documents_quota_blocks_upload(client_a, monkeypatch):
    import app.modules.quota.plans as plans

    monkeypatch.setitem(plans.PLANS["free"], "documents", 1)

    async def _upload(name: str):
        kb_id = (await client_a.post("/api/v1/kbs", json={"name": "配额"})).json()["id"]
        files = {"file": ("a.md", f"# 标题\n\n{name}".encode(), "text/markdown")}
        return await client_a.post(f"/api/v1/kbs/{kb_id}/documents", files=files)

    assert (await _upload("第一个")).status_code == 202
    second = await _upload("第二个")
    assert second.status_code == 429
    body = second.json()["error"]
    assert body["code"] == "QUOTA_EXCEEDED"
    assert body["message"] == "文档数量已达上限"
    assert body["details"]["remaining"] == 0


async def test_query_quota_blocks_chat(client_a, tenant_a, monkeypatch):
    import app.modules.quota.plans as plans

    monkeypatch.setitem(plans.PLANS["free"], "queries_per_month", 1)

    kb_id, _, _ = await seed_document(tenant_a[0], tenant_a[1], [("年假 5 天", basis(0))])

    first = await client_a.post(
        "/api/v1/chat/stream", json={"kb_ids": [str(kb_id)], "query": "年假"}
    )
    assert first.status_code == 200
    await first.aclose()

    second = await client_a.post(
        "/api/v1/chat/stream", json={"kb_ids": [str(kb_id)], "query": "年假"}
    )
    assert second.status_code == 429
    assert second.json()["error"]["message"] == "本月查询次数已用尽"


async def test_quota_report_shape(client_a):
    response = await client_a.get("/api/v1/quota")
    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "free"
    assert set(body["usage"]) == {
        "documents",
        "storage_bytes",
        "queries_per_month",
        "llm_tokens_per_month",
    }
    for item in body["usage"].values():
        assert "used" in item and "limit" in item


async def test_record_usage_persists(tenant_a):
    tenant_id = tenant_a[0]
    async with session_scope(tenant_id=tenant_id) as session:
        await record_usage(session, tenant_id, "doc_ingested", 3)
        rows = (await session.execute(text("SELECT metric, quantity FROM usage_records"))).all()
    assert ("doc_ingested", 3) in rows


async def test_storage_quota(tenant_a, monkeypatch):
    import app.modules.quota.plans as plans

    monkeypatch.setitem(plans.PLANS["free"], "storage_bytes", 10)
    async with session_scope(tenant_id=tenant_a[0]) as session:
        with pytest.raises(Exception) as excinfo:
            await assert_quota(session, tenant_a[0], "storage_bytes", 100)
    assert "存储空间已满" in str(excinfo.value)


async def test_report_reflects_usage(tenant_a):
    tenant_id = tenant_a[0]
    async with session_scope(tenant_id=tenant_id) as session:
        report = await usage_report(session, tenant_id)
    assert report["usage"]["documents"]["used"] == 0
    assert report["usage"]["documents"]["limit"] == 100
