from __future__ import annotations

import uuid

from app.modules.retrieval.context import assemble_context
from app.modules.retrieval.types import Candidate


def _candidate(index: int, doc: str, content: str, score: float, page: int) -> Candidate:
    return Candidate(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc}-{index}"),
        document_id=uuid.uuid5(uuid.NAMESPACE_DNS, doc),
        kb_id=uuid.uuid4(),
        title=doc,
        content=content,
        heading_path="章节",
        page_from=page,
        page_to=page,
        chunk_index=index,
        score=score,
        fused_score=score,
    )


def test_adjacent_chunks_merged():
    doc = "d1"
    candidates = [
        _candidate(0, doc, "第一段", 0.9, 1),
        _candidate(1, doc, "第二段", 0.8, 2),
    ]
    result = assemble_context(candidates)
    assert len(result.blocks) == 1
    assert "第一段" in result.blocks[0].content and "第二段" in result.blocks[0].content
    assert result.blocks[0].page_from == 1
    assert result.blocks[0].page_to == 2


def test_non_adjacent_not_merged():
    doc = "d2"
    candidates = [
        _candidate(0, doc, "甲", 0.9, 1),
        _candidate(5, doc, "乙", 0.8, 2),
    ]
    assert len(assemble_context(candidates).blocks) == 2


def test_indices_start_at_one_and_ordered_by_score():
    candidates = [
        _candidate(0, "d3", "低分", 0.2, 1),
        _candidate(0, "d4", "高分", 0.9, 1),
    ]
    result = assemble_context(candidates)
    assert [block.index for block in result.blocks] == [1, 2]
    assert result.blocks[0].content == "高分"


def test_budget_limits_blocks():
    candidates = [_candidate(i, f"d{i}", "内容" * 400, 0.9 - i * 0.1, 1) for i in range(5)]
    result = assemble_context(candidates, budget_tokens=100)
    assert len(result.blocks) == 1


def test_empty_candidates():
    result = assemble_context([])
    assert result.blocks == []
    assert result.total_tokens == 0
