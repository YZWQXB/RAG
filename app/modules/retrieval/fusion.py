"""RRF（Reciprocal Rank Fusion）融合。

score(d) = Σ 1 / (k + rank_in_list(d))，k 默认 60。
好处是不需要把两路的分数归一化到同一量纲（余弦相似度 vs trigram 相似度不可比）。
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from app.modules.retrieval.types import Candidate

RRF_K = 60


def rrf_fuse(lists: list[list[Candidate]], *, k: int = RRF_K, top_n: int = 20) -> list[Candidate]:
    scores: dict[uuid.UUID, float] = {}
    best: dict[uuid.UUID, Candidate] = {}

    for items in lists:
        for rank, candidate in enumerate(items, start=1):
            if candidate.id not in best:
                best[candidate.id] = candidate
            scores[candidate.id] = scores.get(candidate.id, 0.0) + 1.0 / (k + rank)

    ordered = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:top_n]
    return [replace(best[cid], fused_score=scores[cid]) for cid in ordered]
