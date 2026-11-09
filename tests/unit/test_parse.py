from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ParseError
from app.modules.ingestion.pipeline.parse import (
    parse_document,
    parse_docx,
    parse_markdown,
    parse_pdf,
    parse_txt,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _sample_md() -> bytes:
    return (FIXTURES / "sample.md").read_bytes()


def _make_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 80), "Product Guide", fontsize=20)
    page1.insert_text((72, 120), "This is the body text of page one.", fontsize=11)
    page2 = doc.new_page()
    page2.insert_text((72, 80), "Second page content about warranty.", fontsize=11)
    raw = doc.tobytes()
    doc.close()
    return raw


def test_markdown_heading_path():
    parsed = parse_markdown(_sample_md())
    paths = [block.heading_path for block in parsed.blocks]
    assert "员工手册 > 3 假期 > 3.2 年假" in paths
    assert parsed.page_count == 1


def test_markdown_code_fence_preserved():
    raw = "# 标题\n\n```python\nprint(1)\n```\n".encode()
    parsed = parse_markdown(raw)
    code_blocks = [b for b in parsed.blocks if b.is_code]
    assert len(code_blocks) == 1
    assert "print(1)" in code_blocks[0].text


def test_markdown_table_detected():
    raw = "# 表\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n".encode()
    parsed = parse_markdown(raw)
    tables = [b for b in parsed.blocks if b.is_table]
    assert len(tables) == 1
    assert "| 1 | 2 |" in tables[0].text


def test_pdf_pages_and_heading():
    parsed = parse_pdf(_make_pdf())
    assert parsed.page_count == 2
    assert any(block.page_from == 2 for block in parsed.blocks)
    # 20pt 相对 11pt 正文属于标题
    assert any(block.heading_level >= 1 for block in parsed.blocks)


def test_encrypted_pdf_rejected():
    with pytest.raises(ParseError):
        parse_pdf(b"%PDF-1.4 not really a pdf")


def test_docx_paragraphs():
    import io

    from docx import Document as DocxDocument

    document = DocxDocument()
    document.add_heading("年假制度", level=1)
    document.add_paragraph("员工每年享有 5 天年假。")
    buffer = io.BytesIO()
    document.save(buffer)

    parsed = parse_docx(buffer.getvalue())
    assert parsed.blocks[0].heading_level == 1
    assert "5 天年假" in parsed.blocks[-1].text
    assert parsed.blocks[-1].heading_path == "年假制度"


def test_txt_single_block():
    parsed = parse_txt("纯文本内容。".encode())
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].page_from == 1


def test_unsupported_extension():
    with pytest.raises(ParseError):
        parse_document("a.exe", b"x")


def test_empty_content_rejected():
    with pytest.raises(ParseError):
        parse_txt(b"   \n  ")
