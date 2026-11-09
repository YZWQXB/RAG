from __future__ import annotations

import pytest

from app.core.db import session_scope
from app.modules.retrieval.sparse import sparse_recall
from tests.helpers import basis, seed_document

pytestmark = pytest.mark.db


async def test_matches_sku_code(tenant_a):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id,
        user_id,
        [
            ("SKU-8823 的退换货政策为七天无理由。", basis(0)),
            ("完全无关的一段说明文字。", basis(1)),
        ],
    )

    async with session_scope(tenant_id=tenant_id) as session:
        results = await sparse_recall(session, tenant_id, [kb_id], "SKU-8823")

    assert results
    assert "SKU-8823" in results[0].content


async def test_short_query_returns_empty(tenant_a):
    tenant_id = tenant_a[0]
    async with session_scope(tenant_id=tenant_id) as session:
        assert await sparse_recall(session, tenant_id, [], "a") == []


async def test_no_match_returns_empty_without_error(tenant_a):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [("年假制度", basis(0))])

    async with session_scope(tenant_id=tenant_id) as session:
        results = await sparse_recall(session, tenant_id, [kb_id], "完全不相关的词汇")
    assert results == []


async def test_sparse_isolated_by_tenant(tenant_a, tenant_b):
    tenant_a_id, user_a, _ = tenant_a
    tenant_b_id, user_b, _ = tenant_b

    kb_a, _, _ = await seed_document(tenant_a_id, user_a, [("SKU-1111 属于 A", basis(0))])
    await seed_document(tenant_b_id, user_b, [("SKU-1111 属于 B", basis(0))])

    async with session_scope(tenant_id=tenant_a_id) as session:
        results = await sparse_recall(session, tenant_a_id, [kb_a], "SKU-1111")

    assert all("属于 A" in candidate.content for candidate in results)
