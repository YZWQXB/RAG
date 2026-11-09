"""解析阶段的大小防线。

docx/xlsx 本质是 zip：50MB 的压缩包可以解出几十 GB，
只按上传字节数限制挡不住这种放大攻击。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.config import settings
from app.core.errors import ParseError
from app.modules.ingestion.pipeline.parse import parse_docx


def _zip_with_entry(size: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"\0" * size)
    return buffer.getvalue()


def test_rejects_zip_that_expands_beyond_limit(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_uncompressed_bytes", 4096)
    raw = _zip_with_entry(64 * 1024)
    assert len(raw) < 4096, "测试样本本身必须是压缩后很小的炸弹"

    with pytest.raises(ParseError, match="解压后体积"):
        parse_docx(raw)


def test_allows_zip_within_limit(monkeypatch):
    import docx

    monkeypatch.setattr(settings, "upload_max_uncompressed_bytes", 100 * 1024 * 1024)
    document = docx.Document()
    document.add_paragraph("正常内容")
    buffer = io.BytesIO()
    document.save(buffer)

    parsed = parse_docx(buffer.getvalue())
    assert parsed.blocks[0].text == "正常内容"
