"""检索调试接口。

这是调分块与阈值的唯一可视化依据：能看到两路召回差异、融合前后序、重排分数。
默认在生产环境不挂载（见 api/v1/__init__.py）。返回内容只给 120 字预览，
避免变成批量导出文档的通道。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.retrieval.schemas import DebugRetrieveIn
from app.modules.retrieval.service import retrieve
from app.modules.retrieval.types import Candidate

router = APIRouter(prefix="/debug", tags=["debug"])

PREVIEW_CHARS = 120


def _brief(candidate: Candidate) -> dict:
    return {
        "chunk_id": str(candidate.id),
        "document_id": str(candidate.document_id),
        "title": candidate.title,
        "score": round(candidate.score, 4),
        "fused_score": round(candidate.fused_score, 6),
        "rerank_score": round(candidate.rerank_score, 4),
        "heading_path": candidate.heading_path,
        "page_from": candidate.page_from,
        "preview": candidate.content[:PREVIEW_CHARS],
    }


@router.post("/retrieve")
async def debug_retrieve(
    payload: DebugRetrieveIn, principal: CurrentPrincipal, session: DbSession
) -> dict:
    result = await retrieve(
        session, principal, payload.kb_ids, payload.query, use_rerank=payload.rerank
    )
    return {
        "normalized_query": result.normalized_query,
        "dense": [_brief(c) for c in result.dense],
        "sparse": [_brief(c) for c in result.sparse],
        "fused": [_brief(c) for c in result.fused],
        "reranked": [_brief(c) for c in result.candidates],
        "context": {
            "blocks": [
                {
                    "index": block.index,
                    "document_id": str(block.document_id),
                    "title": block.title,
                    "heading_path": block.heading_path,
                    "page_from": block.page_from,
                    "page_to": block.page_to,
                    "tokens": len(block.content),
                }
                for block in result.context.blocks
            ],
            "total_tokens": result.context.total_tokens,
        },
        "timings_ms": result.timings_ms,
    }
