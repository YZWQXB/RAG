from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Candidate:
    id: uuid.UUID
    document_id: uuid.UUID
    kb_id: uuid.UUID
    title: str
    content: str
    heading_path: str | None
    page_from: int
    page_to: int
    chunk_index: int
    score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float = 0.0

    @property
    def final_score(self) -> float:
        return self.rerank_score or self.fused_score or self.score


@dataclass
class ContextBlock:
    index: int
    document_id: uuid.UUID
    chunk_id: uuid.UUID | None
    title: str
    heading_path: str | None
    page_from: int
    page_to: int
    content: str


@dataclass
class ContextResult:
    blocks: list[ContextBlock] = field(default_factory=list)
    total_tokens: int = 0


@dataclass
class RetrievalResult:
    normalized_query: str
    dense: list[Candidate] = field(default_factory=list)
    sparse: list[Candidate] = field(default_factory=list)
    fused: list[Candidate] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    context: ContextResult = field(default_factory=ContextResult)
    top_dense_score: float = 0.0
    top_sparse_score: float = 0.0
    timings_ms: dict[str, int] = field(default_factory=dict)
