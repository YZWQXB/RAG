from __future__ import annotations

import pytest

from tests.helpers import basis, seed_document

pytestmark = pytest.mark.db


async def test_debug_retrieve_returns_all_stages(client_a, tenant_a):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id,
        user_id,
        [
            ("员工入职满一年可享受 5 天年假。", basis(0)),
            ("报销需在 30 天内提交。", basis(1)),
            ("年假可以分次使用。", basis(2)),
        ],
    )

    response = await client_a.post(
        "/api/v1/debug/retrieve", json={"kb_ids": [str(kb_id)], "query": "年假有多少天"}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    for key in ("normalized_query", "dense", "sparse", "fused", "reranked", "context"):
        assert key in body
    assert body["dense"]
    assert body["context"]["total_tokens"] >= 0
    assert all(len(item["preview"]) <= 120 for item in body["dense"])


async def test_debug_retrieve_filters_unauthorized_kb(client_a, client_b, tenant_a, tenant_b):
    kb_a, _, _ = await seed_document(tenant_a[0], tenant_a[1], [("A 的秘密", basis(0))])
    kb_b, _, _ = await seed_document(tenant_b[0], tenant_b[1], [("B 的秘密", basis(0))])

    response = await client_a.post(
        "/api/v1/debug/retrieve",
        json={"kb_ids": [str(kb_a), str(kb_b)], "query": "秘密"},
    )
    body = response.json()
    assert all(item["document_id"] for item in body["dense"])
    # 只应命中 A 的块
    assert all("A 的秘密" in item["preview"] for item in body["dense"])


async def test_debug_retrieve_requires_auth(anon_client):
    response = await anon_client.post(
        "/api/v1/debug/retrieve",
        json={"kb_ids": ["00000000-0000-0000-0000-000000000001"], "query": "x"},
    )
    assert response.status_code == 401


async def test_retrieve_no_readable_kb_returns_empty(client_a):
    response = await client_a.post(
        "/api/v1/debug/retrieve",
        json={"kb_ids": ["00000000-0000-0000-0000-000000000002"], "query": "年假"},
    )
    body = response.json()
    assert body["dense"] == []
    assert body["context"]["blocks"] == []
