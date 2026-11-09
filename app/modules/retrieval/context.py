"""上下文组装：合并相邻块、按分数装填、受 token 预算约束。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.tokens import count_tokens
from app.modules.retrieval.types import Candidate, ContextBlock, ContextResult


@dataclass
class _Group:
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    title: str
    heading_path: str | None
    page_from: int
    page_to: int
    content: str
    score: float
    last_index: int
    parts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.parts = [self.content]

    def extend(self, candidate: Candidate) -> None:
        self.parts.append(candidate.content)
        self.content = "\n".join(self.parts)
        self.page_from = min(self.page_from, candidate.page_from)
        self.page_to = max(self.page_to, candidate.page_to)
        self.score = max(self.score, candidate.final_score)
        self.last_index = candidate.chunk_index


def assemble_context(
    candidates: list[Candidate], budget_tokens: int | None = None
) -> ContextResult:
    budget = budget_tokens or settings.retrieval_budget_tokens
    if not candidates:
        return ContextResult()

    ordered = sorted(candidates, key=lambda c: (str(c.document_id), c.chunk_index))
    groups: list[_Group] = []
    for candidate in ordered:
        if (
            groups
            and groups[-1].document_id == candidate.document_id
            and candidate.chunk_index == groups[-1].last_index + 1
        ):
            groups[-1].extend(candidate)
            continue
        groups.append(
            _Group(
                document_id=candidate.document_id,
                chunk_id=candidate.id,
                title=candidate.title,
                heading_path=candidate.heading_path,
                page_from=candidate.page_from,
                page_to=candidate.page_to,
                content=candidate.content,
                score=candidate.final_score,
                last_index=candidate.chunk_index,
            )
        )

    groups.sort(key=lambda g: g.score, reverse=True)

    blocks: list[ContextBlock] = []
    total = 0
    for group in groups:
        tokens = count_tokens(group.content)
        if blocks and total + tokens > budget:
            break
        total += tokens
        blocks.append(
            ContextBlock(
                index=len(blocks) + 1,
                document_id=group.document_id,
                chunk_id=group.chunk_id,
                title=group.title,
                heading_path=group.heading_path,
                page_from=group.page_from,
                page_to=group.page_to,
                content=group.content,
            )
        )

    return ContextResult(blocks=blocks, total_tokens=total)
