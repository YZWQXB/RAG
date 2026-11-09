"""引用映射：把模型输出里的 [n] 映射回块与页码。

只读文本、不改文本——后端保留模型原话以便审计，前端负责把 [n] 渲染成可点击上标。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from app.modules.retrieval.types import ContextBlock

_PATTERN = re.compile(r"\[(\d{1,2})\]")


@dataclass(frozen=True)
class CitationRef:
    index: int
    document_id: uuid.UUID
    chunk_id: uuid.UUID | None
    title: str
    heading_path: str | None
    page_from: int


@dataclass
class CitationResult:
    used: list[int] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)


def extract_citations(text: str, blocks: list[ContextBlock]) -> CitationResult:
    seen: set[int] = set()
    ordered: list[int] = []

    for match in _PATTERN.finditer(text):
        index = int(match.group(1))
        if 1 <= index <= len(blocks) and index not in seen:
            seen.add(index)
            ordered.append(index)

    citations = [
        CitationRef(
            index=index,
            document_id=blocks[index - 1].document_id,
            chunk_id=blocks[index - 1].chunk_id,
            title=blocks[index - 1].title,
            heading_path=blocks[index - 1].heading_path,
            page_from=blocks[index - 1].page_from,
        )
        for index in ordered
    ]
    return CitationResult(used=ordered, citations=citations)
