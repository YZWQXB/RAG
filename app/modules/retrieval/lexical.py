"""离线词法召回。

离线模式原本把查询编成「字符袋向量」再和库里的向量算余弦——但库里的向量是
bge-m3 算的，两个空间毫无关系，余弦恒在 0.05 附近，无论阈值多低都会拒答。

正确做法是**不碰向量**：直接用同样的字符袋算法去比库里的文本。
这样索引里存的是哪个模型的向量都不影响离线检索，也不需要重建索引。

局限要写清楚：这是字面匹配，同义改写（「汽车」vs「轿车」）召回不到。
"""

from __future__ import annotations

import math
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.ingestion.models import Chunk, Document
from app.modules.retrieval.types import Candidate

log = get_logger("retrieval.lexical")


# 中文高频虚词与标点：它们几乎出现在每个块里，计入相似度只会稀释真实信号
_STOP = set("的了吗呢是在和有与及什么怎么如何为个我你他它们这那")
_PUNCT = set("[]|:·*#`>-，。！？；：、（）《》“”‘’【】\n\t")


def _bag(text: str) -> Counter[str]:
    """字符袋：剔除虚词、标点与数字后按字符计数。

    数字必须剔除：分块里有大量页码/编号，否则「2026」这类查询会莫名命中。
    """
    return Counter(
        ch
        for ch in text
        if not ch.isspace() and ch not in _STOP and ch not in _PUNCT and not ch.isdigit()
    )


def lexical_score(query: str, text: str) -> float:
    """字符袋相似度 = 余弦 × 查询覆盖率，范围 0~1。

    两个分量缺一不可：
    - 只看余弦（原实现）：长块被稀释，「什么是递归」只拿到 0.20，被误拒
    - 只看覆盖率：单个字命中就算全中，「红烧肉」靠一个「红」字就能骗到 0.25

    实测（958 块真实语料）：应作答 0.12~0.43，应拒答最高 0.026，
    因此离线阈值取 0.10 而不是 bge-m3 的 0.55。
    """
    left = _bag(query)
    right = _bag(text)
    if not left or not right:
        return 0.0

    dot = sum(count * right.get(ch, 0) for ch, count in left.items())
    if dot == 0:
        return 0.0

    left_norm = math.sqrt(sum(count * count for count in left.values()))
    right_norm = math.sqrt(sum(count * count for count in right.values()))
    cosine = dot / (left_norm * right_norm)
    coverage = sum(1 for ch in left if ch in right) / len(left)
    return round(cosine * coverage, 6)


async def lexical_recall(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query: str,
    top_k: int | None = None,
) -> list[Candidate]:
    """在指定知识库内按字符袋相似度取前 top_k 个块。

    扫描上限由 offline_scan_limit 控制：这是离线模式的已知代价，
    大库会慢（每块一次 O(字符数) 计算）。超过上限时记警告，不静默截断。
    """
    if not kb_ids:
        return []

    limit = top_k or settings.retrieval_top_dense
    scan_limit = settings.offline_scan_limit

    rows = (
        await session.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.kb_id,
                Chunk.content,
                Chunk.heading_path,
                Chunk.page_from,
                Chunk.page_to,
                Chunk.chunk_index,
                Document.title,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.tenant_id == tenant_id,
                Chunk.kb_id.in_(kb_ids),
                Document.deleted_at.is_(None),
            )
            .limit(scan_limit)
        )
    ).all()

    if not rows:
        return []

    scored: list[Candidate] = []
    for row in rows:
        score = lexical_score(query, row.content)
        if score <= 0:
            continue
        scored.append(
            Candidate(
                id=row.id,
                document_id=row.document_id,
                kb_id=row.kb_id,
                title=row.title,
                content=row.content,
                heading_path=row.heading_path,
                page_from=row.page_from,
                page_to=row.page_to,
                chunk_index=row.chunk_index,
                score=score,
            )
        )

    if len(rows) >= scan_limit:
        log.warning("lexical_scan_capped", scanned=len(rows), limit=scan_limit)

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]
