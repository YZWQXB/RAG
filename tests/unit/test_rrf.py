from __future__ import annotations

import uuid

from app.modules.retrieval.fusion import rrf_fuse
from app.modules.retrieval.types import Candidate


def _candidate(name: str) -> Candidate:
    return Candidate(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        document_id=uuid.uuid4(),
        kb_id=uuid.uuid4(),
        title="doc",
        content=f"content-{name}",
        heading_path=None,
        page_from=1,
        page_to=1,
        chunk_index=0,
        score=0.5,
    )


def test_same_id_ranked_first_in_both_lists():
    a = _candidate("a")
    fused = rrf_fuse([[a], [a]], top_n=10)
    assert len(fused) == 1
    assert abs(fused[0].fused_score - (1 / 61 + 1 / 61)) < 1e-9


def test_single_list_contributes_once():
    a = _candidate("a")
    fused = rrf_fuse([[a], []], top_n=10)
    assert abs(fused[0].fused_score - 1 / 61) < 1e-9


def test_overlap_ranks_higher():
    shared = _candidate("shared")
    only_dense = _candidate("dense")
    only_sparse = _candidate("sparse")
    fused = rrf_fuse([[shared, only_dense], [shared, only_sparse]], top_n=10)
    assert fused[0].id == shared.id
    assert len(fused) == 3


def test_top_n_truncates():
    lists = [[_candidate(f"x{i}") for i in range(10)]]
    assert len(rrf_fuse(lists, top_n=4)) == 4


def test_empty_input():
    assert rrf_fuse([[], []], top_n=10) == []
