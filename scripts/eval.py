"""检索质量回归。

指标：
- recall@5：前 5 条命中的标注块数 / 标注块数，对 fact/compare/cross 平均
- MRR：首个命中排名的倒数，未命中记 0
- no_answer 准确率：无答案样本被正确判为"资料不足"的比例

用法：
    uv run python -m scripts.build_eval_corpus   # 首次或语料变更后
    uv run python -m scripts.eval               # 与基线比对
    uv run python -m scripts.eval --write-baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.core.db import dispose_engines, session_scope
from app.core.security import Principal
from app.modules.retrieval.service import retrieve

DATASET = Path("tests/eval/dataset.jsonl")
CORPUS = Path("tests/eval/corpus.json")
BASELINE = Path("tests/eval/baseline.json")
LAST_RUN = Path("tests/eval/last_run.json")

RECALL_K = 5
REGRESSION_TOLERANCE = 0.02


def _load_dataset() -> list[dict]:
    items = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def _resolve_relevant(item: dict, corpus: list[dict]) -> set[str]:
    """按子串匹配把标注解析成 chunk_id，避免把随机 UUID 写进数据集。"""
    found: set[str] = set()
    for needle in item.get("relevant_contains") or []:
        for entry in corpus:
            if needle in entry["content"]:
                found.add(entry["chunk_id"])
    return found


async def _eval_tenant() -> tuple:
    from app.core.db import admin_engine

    async with admin_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT t.id, m.user_id FROM tenants t "
                        "JOIN memberships m ON m.tenant_id = t.id "
                        "JOIN users u ON u.id = m.user_id WHERE u.email = 'eval@example.com'"
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise SystemExit("未找到评测租户，请先运行: uv run python -m scripts.build_eval_corpus")
    return row["id"], row["user_id"]


async def run() -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    dataset = _load_dataset()
    tenant_id, user_id = await _eval_tenant()
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")

    from sqlalchemy import select

    from app.modules.knowledge.models import KnowledgeBase

    async with session_scope(tenant_id=tenant_id) as session:
        kb_ids = list(
            (
                await session.execute(
                    select(KnowledgeBase.id).where(KnowledgeBase.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )

    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    no_answer_hits = 0
    no_answer_total = 0
    details: list[dict] = []

    for item in dataset:
        async with session_scope(tenant_id=tenant_id) as session:
            result = await retrieve(session, principal, kb_ids, item["query"])

        retrieved = [str(candidate.id) for candidate in result.candidates]
        top5 = retrieved[:RECALL_K]
        predicted_no_answer = (
            not result.context.blocks
            or result.top_dense_score < settings.retrieval_no_answer_threshold
        )

        if item["type"] == "no_answer":
            no_answer_total += 1
            if predicted_no_answer:
                no_answer_hits += 1
            details.append(
                {"query": item["query"], "type": item["type"], "no_answer": predicted_no_answer}
            )
            continue

        relevant = _resolve_relevant(item, corpus)
        if not relevant:
            continue

        hit = len(relevant & set(top5)) / len(relevant)
        recalls.append(hit)

        rank = next(
            (index for index, chunk_id in enumerate(retrieved, start=1) if chunk_id in relevant),
            0,
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        details.append(
            {
                "query": item["query"],
                "type": item["type"],
                "recall@5": round(hit, 3),
                "first_hit_rank": rank,
            }
        )

    metrics = {
        "recall@5": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4) if reciprocal_ranks else 0.0,
        "no_answer_accuracy": round(no_answer_hits / no_answer_total, 4)
        if no_answer_total
        else 1.0,
        "samples": len(details),
    }
    return {"metrics": metrics, "details": details}


def compare(metrics: dict, baseline: dict) -> list[str]:
    problems: list[str] = []
    base_recall = baseline.get("recall@5")
    if base_recall is not None and metrics["recall@5"] < base_recall - REGRESSION_TOLERANCE:
        problems.append(
            f"recall@5 退化：{metrics['recall@5']} < 基线 {base_recall} - {REGRESSION_TOLERANCE}"
        )
    if metrics["no_answer_accuracy"] < 1.0:
        problems.append(f"no_answer 准确率不足：{metrics['no_answer_accuracy']}")
    return problems


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    result = await run()
    metrics = result["metrics"]
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"recall@5={metrics['recall@5']} mrr={metrics['mrr']} "
        f"no_answer={metrics['no_answer_accuracy']} samples={metrics['samples']}"
    )

    if args.write_baseline or not BASELINE.exists():
        BASELINE.write_text(
            json.dumps(
                {
                    **metrics,
                    "note": "由 scripts.eval 生成，记录当时的分块与检索参数",
                    "chunk_size_chars": settings.chunk_size_chars,
                    "top_dense": settings.retrieval_top_dense,
                    "top_final": settings.retrieval_top_final,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"baseline written: {BASELINE}")
    else:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        problems = compare(metrics, baseline)
        for problem in problems:
            print(f"REGRESSION: {problem}", file=sys.stderr)
        if problems:
            for detail in result["details"]:
                if detail.get("recall@5") == 0 or detail.get("first_hit_rank") == 0:
                    print(f"  miss: {detail['query']}", file=sys.stderr)
            await dispose_engines()
            raise SystemExit(1)

    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
