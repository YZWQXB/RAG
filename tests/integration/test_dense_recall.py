from __future__ import annotations

import pytest

from app.core.db import session_scope
from app.modules.retrieval.dense import dense_recall
from tests.helpers import basis, seed_document

pytestmark = pytest.mark.db


async def test_dense_recall_orders_by_similarity(tenant_a):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, chunk_ids = await seed_document(
        tenant_id,
        user_id,
        [
            ("第一个块", basis(0)),
            ("第二个块", basis(1)),
            ("第三个块", basis(2)),
        ],
    )

    async with session_scope(tenant_id=tenant_id) as session:
        results = await dense_recall(session, tenant_id, [kb_id], basis(0))

    assert results[0].id == chunk_ids[0]
    assert results[0].score == pytest.approx(1.0, abs=1e-6)
    assert results[1].score == pytest.approx(0.0, abs=1e-6)


async def test_dense_recall_isolated_by_tenant(tenant_a, tenant_b):
    tenant_a_id, user_a, _ = tenant_a
    tenant_b_id, user_b, _ = tenant_b

    kb_a, _, _ = await seed_document(tenant_a_id, user_a, [("A 的内容", basis(0))])
    await seed_document(tenant_b_id, user_b, [("B 的内容", basis(0))])

    async with session_scope(tenant_id=tenant_a_id) as session:
        results = await dense_recall(session, tenant_a_id, [kb_a], basis(0))

    assert all(candidate.kb_id == kb_a for candidate in results)
    assert all(candidate.content == "A 的内容" for candidate in results)


async def test_dense_recall_excludes_deleted_document(tenant_a):
    from app.core.db import session_scope as scope
    from app.modules.ingestion.models import Document

    tenant_id, user_id, _ = tenant_a
    kb_id, doc_id, _ = await seed_document(tenant_id, user_id, [("将被删除", basis(0))])

    async with scope(tenant_id=tenant_id) as session:
        document = await session.get(Document, doc_id)
        from datetime import UTC, datetime

        document.deleted_at = datetime.now(UTC)
        await session.flush()

    async with scope(tenant_id=tenant_id) as session:
        results = await dense_recall(session, tenant_id, [kb_id], basis(0))
    assert results == []


async def test_dense_recall_empty_kb_list(tenant_a):
    tenant_id = tenant_a[0]
    async with session_scope(tenant_id=tenant_id) as session:
        assert await dense_recall(session, tenant_id, [], basis(0)) == []
