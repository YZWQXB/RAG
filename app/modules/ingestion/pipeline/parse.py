"""文档解析：把四类格式统一成带页码与标题层级的块序列。"""

from __future__ import annotations

import io
import re
import statistics
import zipfile
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.errors import ParseError


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    page_from: int
    page_to: int
    heading_level: int = 0
    heading_path: str = ""
    is_table: bool = False
    is_code: bool = False


@dataclass
class ParsedDocument:
    blocks: list[ParsedBlock] = field(default_factory=list)
    page_count: int = 1


class _HeadingTracker:
    """维护标题栈，输出 "A > B > C" 形式。"""

    def __init__(self) -> None:
        self.stack: list[tuple[int, str]] = []

    def update(self, level: int, title: str) -> str:
        self.stack = [item for item in self.stack if item[0] < level]
        self.stack.append((level, title.strip()))
        return self.path

    @property
    def path(self) -> str:
        return " > ".join(title for _, title in self.stack)


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\u3000", " ")).strip()


def parse_pdf(raw: bytes) -> ParsedDocument:
    import fitz

    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise ParseError("文档已损坏，无法解析") from exc

    if doc.needs_pass:
        raise ParseError("文档已加密，无法解析")
    if doc.page_count == 0:
        raise ParseError("文档内容为空")

    sizes: list[float] = []
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("size"):
                        sizes.append(float(span["size"]))

    if not sizes:
        raise ParseError("未能从文档中提取到文本（可能是扫描件）")

    body_size = statistics.median(sizes)
    threshold = body_size * 1.15

    blocks: list[ParsedBlock] = []
    tracker = _HeadingTracker()

    for page_index, page in enumerate(doc, start=1):
        for raw_block in page.get_text("dict").get("blocks", []):
            lines = raw_block.get("lines", [])
            if not lines:
                continue
            texts: list[str] = []
            max_size = 0.0
            for line in lines:
                for span in line.get("spans", []):
                    max_size = max(max_size, float(span.get("size", 0)))
                    texts.append(span.get("text", ""))
            text = _clean("".join(texts))
            if not text:
                continue

            if max_size >= threshold and len(text) <= 80:
                level = 1 if max_size >= body_size * 1.5 else 2
                path = tracker.update(level, text)
                blocks.append(
                    ParsedBlock(
                        text=text,
                        page_from=page_index,
                        page_to=page_index,
                        heading_level=level,
                        heading_path=path,
                    )
                )
            else:
                blocks.append(
                    ParsedBlock(
                        text=text,
                        page_from=page_index,
                        page_to=page_index,
                        heading_path=tracker.path,
                    )
                )

    return ParsedDocument(blocks=blocks, page_count=doc.page_count)


def _guard_zip_budget(raw: bytes) -> None:
    """docx/xlsx 本质是 zip，压缩比可以极高。

    只限制上传字节数挡不住"50MB 解出几十 GB"的解压炸弹，
    所以在交给 python-docx 之前先按**声明**的解压后大小做一次拦截。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ParseError("Word 文档已损坏，无法解析") from exc

    if total > settings.upload_max_uncompressed_bytes:
        raise ParseError("文档解压后体积超出上限，已拒绝解析")


def parse_docx(raw: bytes) -> ParsedDocument:
    from docx import Document as DocxDocument

    _guard_zip_budget(raw)

    try:
        document = DocxDocument(io.BytesIO(raw))
    except Exception as exc:
        raise ParseError("Word 文档已损坏，无法解析") from exc

    tracker = _HeadingTracker()
    blocks: list[ParsedBlock] = []

    for item in document.iter_inner_content():
        if item.__class__.__name__ == "Table":
            rows = []
            for row in item.rows:
                cells = [_clean(cell.text) for cell in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            blocks.append(
                ParsedBlock(
                    text="\n".join(rows),
                    page_from=1,
                    page_to=1,
                    heading_path=tracker.path,
                    is_table=True,
                )
            )
            continue

        text = _clean(item.text)
        if not text:
            continue
        style = (item.style.name or "") if item.style is not None else ""
        match = re.match(r"Heading (\d)", style)
        if match:
            level = min(int(match.group(1)), 3)
            path = tracker.update(level, text)
            blocks.append(
                ParsedBlock(
                    text=text,
                    page_from=1,
                    page_to=1,
                    heading_level=level,
                    heading_path=path,
                )
            )
        else:
            blocks.append(ParsedBlock(text=text, page_from=1, page_to=1, heading_path=tracker.path))

    if not blocks:
        raise ParseError("文档内容为空")
    return ParsedDocument(blocks=blocks, page_count=1)


def parse_markdown(raw: bytes) -> ParsedDocument:
    text = raw.decode("utf-8", errors="replace")
    tracker = _HeadingTracker()
    blocks: list[ParsedBlock] = []

    buffer: list[str] = []
    in_code = False
    code_lines: list[str] = []
    table_lines: list[str] = []

    def flush_text() -> None:
        if buffer:
            joined = _clean(" ".join(buffer))
            if joined:
                blocks.append(
                    ParsedBlock(text=joined, page_from=1, page_to=1, heading_path=tracker.path)
                )
            buffer.clear()

    def flush_table() -> None:
        if table_lines:
            blocks.append(
                ParsedBlock(
                    text="\n".join(table_lines),
                    page_from=1,
                    page_to=1,
                    heading_path=tracker.path,
                    is_table=True,
                )
            )
            table_lines.clear()

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                blocks.append(
                    ParsedBlock(
                        text="\n".join(code_lines),
                        page_from=1,
                        page_to=1,
                        heading_path=tracker.path,
                        is_code=True,
                    )
                )
                code_lines.clear()
                in_code = False
            else:
                flush_text()
                flush_table()
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_text()
            flush_table()
            level = min(len(heading.group(1)), 3)
            path = tracker.update(level, heading.group(2))
            blocks.append(
                ParsedBlock(
                    text=_clean(heading.group(2)),
                    page_from=1,
                    page_to=1,
                    heading_level=level,
                    heading_path=path,
                )
            )
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_text()
            table_lines.append(stripped)
            continue

        if not stripped:
            flush_text()
            flush_table()
            continue

        flush_table()
        buffer.append(stripped)

    flush_text()
    flush_table()

    if not blocks:
        raise ParseError("文档内容为空")
    return ParsedDocument(blocks=blocks, page_count=1)


def parse_txt(raw: bytes) -> ParsedDocument:
    text = raw.decode("utf-8", errors="replace")
    cleaned = _clean(text)
    if not cleaned:
        raise ParseError("文档内容为空")
    return ParsedDocument(blocks=[ParsedBlock(text=cleaned, page_from=1, page_to=1)], page_count=1)


PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_txt,
}


def parse_document(filename: str, raw: bytes) -> ParsedDocument:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    parser = PARSERS.get(suffix)
    if parser is None:
        raise ParseError("暂不支持该文件类型")
    return parser(raw)
