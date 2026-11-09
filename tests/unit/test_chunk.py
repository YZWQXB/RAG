from __future__ import annotations

from pathlib import Path

from app.modules.ingestion.pipeline.chunk import chunk_document
from app.modules.ingestion.pipeline.parse import parse_markdown

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _parsed():
    return parse_markdown((FIXTURES / "sample.md").read_bytes())


def test_chunk_count_and_order():
    drafts = chunk_document(_parsed(), "员工手册")
    assert len(drafts) == 3
    assert [d.chunk_index for d in drafts] == [0, 1, 2]


def test_heading_path_preserved():
    drafts = chunk_document(_parsed(), "员工手册")
    assert drafts[1].heading_path == "员工手册 > 3 假期 > 3.2 年假"
    assert "5 天年假" in drafts[1].content


def test_context_header_prepended():
    drafts = chunk_document(_parsed(), "员工手册")
    assert all(d.content.startswith("[文档: 员工手册 | 章节: ") for d in drafts)


def test_header_can_be_disabled():
    drafts = chunk_document(_parsed(), "员工手册", {"prepend_context_header": False})
    assert not any(d.content.startswith("[文档:") for d in drafts)


def test_deterministic_hashes():
    first = [d.content_hash for d in chunk_document(_parsed(), "员工手册")]
    second = [d.content_hash for d in chunk_document(_parsed(), "员工手册")]
    assert first == second


def test_respects_size_budget():
    long_text = "# 长文\n\n" + ("这是一段很长的说明文字。" * 200)
    parsed = parse_markdown(long_text.encode())
    drafts = chunk_document(parsed, "长文", {"chunk_size_chars": 300, "overlap_chars": 50})
    assert len(drafts) > 1
    for draft in drafts:
        assert len(draft.content) <= 300 + 50 + 60  # 预留上下文头长度


def test_table_not_split():
    raw = "# 表\n\n| a | b |\n| --- | --- |\n" + "".join(
        f"| {i} | value-{i} |\n" for i in range(30)
    )
    parsed = parse_markdown(raw.encode())
    drafts = chunk_document(parsed, "表", {"chunk_max_chars": 5000})
    table_chunks = [d for d in drafts if d.content.startswith("[文档")]
    assert len(table_chunks) == 1


def test_token_count_positive():
    drafts = chunk_document(_parsed(), "员工手册")
    assert all(d.token_count > 0 for d in drafts)
