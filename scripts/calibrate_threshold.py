"""用评测集标定"无答案"阈值。

`retrieval_no_answer_threshold` 决定"资料不足时是否拒答"，拍一个数字上去
会出现两种相反的错：该拒答却硬答（幻觉）、该回答却拒答（不可用）。
这个脚本用带标注的查询集扫一遍阈值网格，给出分离度最好的取值。

用法：
    uv run python -m scripts.build_eval_corpus     # 首次需要
    uv run python -m scripts.calibrate_threshold
    uv run python -m scripts.calibrate_threshold --write   # 写入 tests/eval/threshold.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import settings
from app.core.db import admin_engine, dispose_engines, session_scope
from app.core.security import Principal
from app.modules.knowledge.models import KnowledgeBase
from app.modules.retrieval.service import retrieve

DATASET = Path("tests/eval/dataset.jsonl")
CORPUS = Path("tests/eval/corpus.json")
OUTPUT = Path("tests/eval/threshold.json")

GRID = [round(0.05 * i, 2) for i in range(1, 17)]  # 0.05 ~ 0.80


def _load_dataset() -> list[dict]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _eval_principal() -> tuple:
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


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    if not CORPUS.exists():
        raise SystemExit("缺少 tests/eval/corpus.json，请先运行 build_eval_corpus")

    dataset = _load_dataset()
    tenant_id, user_id = await _eval_principal()
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")

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

    answerable_scores: list[float] = []
    no_answer_scores: list[float] = []
    details: list[dict] = []

    for item in dataset:
        async with session_scope(tenant_id=tenant_id) as session:
            result = await retrieve(session, principal, kb_ids, item["query"], use_rerank=False)
        score = result.top_dense_score
        is_no_answer = item["type"] == "no_answer"
        (no_answer_scores if is_no_answer else answerable_scores).append(score)
        details.append(
            {"query": item["query"], "type": item["type"], "top_dense_score": round(score, 4)}
        )

    if settings.embedding_provider == "fake":
        print("[warn] 当前使用假向量，分数没有语义意义，标定结果不可用于生产")

    def pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(len(ordered) * p))], 4)

    grid = []
    for threshold in GRID:
        correct = sum(1 for s in answerable_scores if s >= threshold) + sum(
            1 for s in no_answer_scores if s < threshold
        )
        total = len(answerable_scores) + len(no_answer_scores)
        grid.append(
            {
                "threshold": threshold,
                "accuracy": round(correct / total, 4) if total else 0.0,
            }
        )

    best = max(grid, key=lambda item: (item["accuracy"], -item["threshold"]))

    summary = {
        "current_threshold": settings.retrieval_no_answer_threshold,
        "suggested_threshold": best["threshold"],
        "accuracy": best["accuracy"],
        "answerable": {
            "count": len(answerable_scores),
            "min": round(min(answerable_scores), 4) if answerable_scores else 0.0,
            "p50": pct(answerable_scores, 0.5),
            "p90": pct(answerable_scores, 0.9),
        },
        "no_answer": {
            "count": len(no_answer_scores),
            "max": round(max(no_answer_scores), 4) if no_answer_scores else 0.0,
            "p50": pct(no_answer_scores, 0.5),
        },
        "grid": grid,
        "details": details,
        "provider": settings.embedding_provider,
    }

    print(
        f"[summary] 可答 top1 p50={summary['answerable']['p50']} "
        f"不可答 top1 max={summary['no_answer']['max']}"
    )
    print(
        f"[suggest] threshold={best['threshold']} "
        f"accuracy={best['accuracy']} (当前 {settings.retrieval_no_answer_threshold})"
    )

    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] {OUTPUT}")

    await dispose_engines()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
