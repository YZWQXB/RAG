"""拒答判定：稠密与稀疏两路都要算数。

原实现只看 dense[0].score，导致「字面精确命中但语义分低」的查询被误拒；
离线 fake 向量下 dense 恒在 0.3~0.4，配 0.55 阈值会 100% 拒答。
"""

from __future__ import annotations

from app.core.config import settings
from app.modules.retrieval.service import answerable


def test_sparse_hit_overrides_low_dense(monkeypatch):
    """精确编号类查询：稠密分低，但 pg_trgm 命中很高。"""
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.55)
    monkeypatch.setattr(settings, "retrieval_sparse_answer_threshold", 0.35)

    assert answerable(top_dense=0.20, top_sparse=0.62) is True


def test_both_channels_low_refuses(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.55)
    monkeypatch.setattr(settings, "retrieval_sparse_answer_threshold", 0.35)

    assert answerable(top_dense=0.41, top_sparse=0.10) is False


def test_dense_hit_answers_without_sparse(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.55)

    assert answerable(top_dense=0.79, top_sparse=0.0) is True


def test_fake_embedding_uses_lower_threshold(monkeypatch):
    """离线模式必须用为字符袋标定的阈值，否则永远拒答。"""
    monkeypatch.setattr(settings, "embedding_provider", "fake")
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.55)
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold_fake", 0.10)

    assert answerable(top_dense=0.24, top_sparse=0.0) is True
    assert answerable(top_dense=0.05, top_sparse=0.0) is False


def test_online_provider_keeps_strict_threshold(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.55)
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold_fake", 0.10)

    assert answerable(top_dense=0.33, top_sparse=0.0) is False
