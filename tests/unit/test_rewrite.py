from __future__ import annotations

import uuid

from app.modules.retrieval.rerank import should_rerank
from app.modules.retrieval.rewrite import normalize_query
from app.modules.retrieval.types import Candidate


def _candidate(score: float) -> Candidate:
    return Candidate(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        kb_id=uuid.uuid4(),
        title="d",
        content="c",
        heading_path=None,
        page_from=1,
        page_to=1,
        chunk_index=0,
        score=score,
    )


def test_normalize_strips_filler_and_punctuation():
    assert normalize_query("  请问年假有多少天？ ") == "年假有多少天"


def test_normalize_collapses_whitespace():
    assert normalize_query("年假   有   几天") == "年假 有 几天"


def test_normalize_keeps_content_when_only_filler():
    assert normalize_query("请问") == "请问"


def test_normalize_truncates_long_query():
    assert len(normalize_query("啊" * 900)) <= 512


def test_skip_rerank_for_few_candidates():
    assert should_rerank("一个很长的问题需要重排吗", [_candidate(0.1)] * 2) is False


def test_skip_rerank_for_confident_short_query():
    assert should_rerank("年假几天", [_candidate(0.9)] * 5) is False


def test_rerank_for_longer_query_with_low_score():
    assert should_rerank("跨境订单的退款政策具体是怎么规定的", [_candidate(0.4)] * 5) is True


def test_rerank_when_low_confidence_even_short():
    assert should_rerank("年假", [_candidate(0.1)] * 5) is True
