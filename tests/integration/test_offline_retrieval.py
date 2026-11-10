"""离线检索必须在「索引是别的模型建的」情况下也能工作。

这是用户实际遇到的场景：库里的分块是 bge-m3 算的向量，切到离线后
查询向量来自字符袋，两者毫无关系，余弦恒 0.05，阈值多低都拒答。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.db import session_scope
from app.core.security import Principal
from app.modules.retrieval.service import answerable, retrieve
from tests.helpers import seed_document

pytestmark = pytest.mark.db

CONTENT = "递归是指函数直接或间接调用自身，递归必须有终止条件。"
FOREIGN_VECTOR = [0.03125] * 1024  # 模拟 bge-m3 建的索引（单位向量，与字符袋无关）


async def test_offline_uses_lexical_when_vectors_are_foreign(tenant_a, monkeypatch):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, FOREIGN_VECTOR)])

    monkeypatch.setattr(settings, "embedding_provider", "fake")
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")

    async with session_scope(tenant_id=tenant_id) as session:
        result = await retrieve(session, principal, [kb_id], "什么是递归")

    assert result.top_dense_score >= settings.retrieval_no_answer_threshold_fake
    assert answerable(result.top_dense_score, result.top_sparse_score) is True
    assert "递归" in result.context.blocks[0].content


async def test_offline_still_refuses_unrelated(tenant_a, monkeypatch):
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CONTENT, FOREIGN_VECTOR)])

    monkeypatch.setattr(settings, "embedding_provider", "fake")
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")

    async with session_scope(tenant_id=tenant_id) as session:
        result = await retrieve(session, principal, [kb_id], "红烧肉怎么做")

    assert answerable(result.top_dense_score, result.top_sparse_score) is False
