"""分块：标题层级优先 + 长度兜底 + 句边界修正。

与设计文档的换算关系（不要改）：
设计文档写的是 400–600 tokens，中文约 1.5 字/token，即 600–900 字。
本实现取 chunk_size_chars=600（约 400 token）作为保守下界——宁可块偏小，
以保证引用能定位到较窄的原文范围。调参走 knowledge_bases.chunk_config，不改代码。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.core.tokens import count_tokens
from app.modules.ingestion.pipeline.parse import ParsedDocument

DEFAULT_CONFIG = {
    "chunk_size_chars": 600,
    "overlap_chars": 90,
    "max_chars": 1200,
    "prepend_context_header": True,
}

_BREAK_CHARS = "。！？!?\n"


@dataclass(frozen=True)
class ChunkDraft:
    content: str
    chunk_index: int
    heading_path: str
    page_from: int
    page_to: int
    token_count: int
    content_hash: str


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _cut(text: str, size: int, overlap: int) -> tuple[str, str]:
    """切出不超过 size 的一段，返回 (chunk_text, carry_over)。

    carry_over 是下一块开头要重复的尾部内容，长度约 overlap。
    """
    if len(text) <= size:
        return text, ""

    window = text[:size]
    break_at = max(window.rfind(ch) for ch in _BREAK_CHARS)
    if break_at < size // 2:
        break_at = size
    else:
        break_at += 1

    head = text[:break_at]
    tail_start = max(break_at - overlap, 0) if overlap else break_at
    return head, text[tail_start:]


def _with_header(text: str, title: str, heading_path: str, enabled: bool) -> str:
    if not enabled:
        return text
    section = heading_path or "正文"
    return f"[文档: {title} | 章节: {section}]\n{text}"


def chunk_document(
    parsed: ParsedDocument, title: str, config: dict | None = None
) -> list[ChunkDraft]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    size = int(cfg["chunk_size_chars"])
    overlap = int(cfg["overlap_chars"])
    max_chars = int(cfg["max_chars"])
    header_enabled = bool(cfg["prepend_context_header"])

    drafts: list[ChunkDraft] = []
    buffer = ""
    buffer_pages: list[tuple[int, int]] = []

    def emit(text: str, heading_path: str, pages: list[tuple[int, int]]) -> None:
        body = normalize(text)
        if not body:
            return
        page_from = min((p[0] for p in pages), default=1)
        page_to = max((p[1] for p in pages), default=page_from)
        content = _with_header(body, title, heading_path, header_enabled)
        drafts.append(
            ChunkDraft(
                content=content,
                chunk_index=len(drafts),
                heading_path=heading_path,
                page_from=page_from,
                page_to=page_to,
                token_count=count_tokens(content),
                content_hash=content_hash(content),
            )
        )

    def flush() -> str:
        nonlocal buffer, buffer_pages
        if buffer.strip():
            emit(buffer, current_path, buffer_pages or [(1, 1)])
        buffer = ""
        buffer_pages = []
        return ""

    current_path = ""

    for block in parsed.blocks:
        if block.heading_level > 0:
            # 标题文本已体现在 heading_path 中，不重复计入正文
            flush()
            current_path = block.heading_path
            continue

        if block.heading_path != current_path:
            flush()
            current_path = block.heading_path

        if block.is_table or block.is_code:
            flush()
            text = block.text
            while len(text) > max_chars:
                head, text = _cut(text, max_chars, 0)
                emit(head, current_path, [(block.page_from, block.page_to)])
            emit(text, current_path, [(block.page_from, block.page_to)])
            continue

        page = (block.page_from, block.page_to)
        candidate = f"{buffer} {block.text}".strip() if buffer else block.text
        pages = [*buffer_pages, page]

        while len(candidate) > size:
            head, carry = _cut(candidate, size, overlap)
            emit(head, current_path, pages)
            candidate = carry
            pages = [page]

        buffer = candidate
        buffer_pages = pages

    flush()
    return drafts
