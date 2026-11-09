from __future__ import annotations

import uuid

from app.modules.generation.citations import extract_citations
from app.modules.retrieval.types import ContextBlock


def _blocks(count: int = 3) -> list[ContextBlock]:
    return [
        ContextBlock(
            index=i,
            document_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            title=f"文档{i}",
            heading_path=None,
            page_from=i,
            page_to=i,
            content="x",
        )
        for i in range(1, count + 1)
    ]


def test_dedupes_and_preserves_order():
    result = extract_citations("答案见 [1] 与 [2]，另见 [1]", _blocks())
    assert result.used == [1, 2]
    assert len(result.citations) == 2


def test_out_of_range_dropped():
    result = extract_citations("引用 [9]", _blocks(3))
    assert result.used == []


def test_no_markers():
    assert extract_citations("没有引用", _blocks()).used == []


def test_double_digit_index():
    result = extract_citations("见 [12]", _blocks(20))
    assert result.used == [12]


def test_year_not_treated_as_citation_out_of_range():
    result = extract_citations("2024 年的政策 [2024]", _blocks(3))
    # [2024] 超过两位数字，正则不匹配；即使匹配也会因越界被丢弃
    assert result.used == []


def test_citation_maps_page_and_document():
    blocks = _blocks(3)
    result = extract_citations("[3]", blocks)
    assert result.citations[0].page_from == 3
    assert result.citations[0].document_id == blocks[2].document_id
    assert result.citations[0].title == "文档3"
