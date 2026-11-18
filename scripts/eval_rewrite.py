"""多轮指代消解的效果评测。

## 这个脚本测什么，不测什么

测：**改写的收益上限**。用人工标注的改写结果替换模型输出，看检索质量能提升多少。
这样把「改写器好不好」和「改写有没有用」拆开——如果连金标准改写都提不了分，
那就该关掉这个功能，而不是去调模型。

不测：真模型的改写质量。那需要在线环境与 token 预算，属于上线前的灰度指标，
用 `rag_query_rewrite_total{result}` 观察线上命中率与拒绝率更实际。

## 指标

- bare_recall@5      直接用追问（「那满三年呢」）检索的命中率
- rewritten_recall@5 用标注改写（「工作满三年后年假多少天」）检索的命中率
- lift               两者差值，就是改写带来的收益
- gate_accuracy      `needs_rewrite` 的门控判断与人工标注的一致率

用法：
    uv run python -m scripts.eval_rewrite
    uv run python -m scripts.eval_rewrite --check   # CI 用
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.db import dispose_engines, session_scope
from app.core.security import Principal
from app.modules.retrieval.rewrite import needs_rewrite
from app.modules.retrieval.service import retrieve
from scripts.eval import RECALL_K, _eval_tenant, _resolve_relevant

DATASET = Path("tests/eval/multiturn.jsonl")
CORPUS = Path("tests/eval/corpus.json")
REPORT = Path("docs/reports/eval-rewrite-latest.md")

# 门控是成本闸门：漏判只是多检索一次，误判会白烧一次 token 且可能改错。
# 因此对准确率的要求偏严。
MIN_GATE_ACCURACY = 0.9


def _load() -> list[dict]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _recall(principal, kb_ids, query: str, relevant: set[str]) -> float:
    async with session_scope(tenant_id=principal.tenant_id) as session:
        result = await retrieve(session, principal, kb_ids, query)
    top5 = {str(candidate.id) for candidate in result.candidates[:RECALL_K]}
    return len(relevant & top5) / len(relevant)


async def run() -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    items = _load()
    tenant_id, user_id = await _eval_tenant()
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")

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

    bare_scores: list[float] = []
    rewritten_scores: list[float] = []
    gate_hits = 0
    details: list[dict] = []

    for item in items:
        relevant = _resolve_relevant(item, corpus)
        if not relevant:
            raise SystemExit(f"标注解析不到分块，请重建语料或修正标注：{item['query']}")

        bare = await _recall(principal, kb_ids, item["query"], relevant)
        rewritten = await _recall(principal, kb_ids, item["rewritten"], relevant)
        gate = needs_rewrite(item["query"], item["history"])
        gate_ok = gate == item["should_rewrite"]

        bare_scores.append(bare)
        rewritten_scores.append(rewritten)
        gate_hits += int(gate_ok)
        details.append(
            {
                "query": item["query"],
                "rewritten": item["rewritten"],
                "type": "should_rewrite" if item["should_rewrite"] else "should_skip",
                "bare_recall@5": round(bare, 3),
                "rewritten_recall@5": round(rewritten, 3),
                "gate": gate,
                "gate_ok": gate_ok,
            }
        )

    def _avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    bare_avg = _avg(bare_scores)
    rewritten_avg = _avg(rewritten_scores)
    return {
        "metrics": {
            "bare_recall@5": bare_avg,
            "rewritten_recall@5": rewritten_avg,
            "lift": round(rewritten_avg - bare_avg, 4),
            "gate_accuracy": round(gate_hits / len(items), 4),
            "samples": len(items),
        },
        "details": details,
    }


def _render(result: dict, problems: list[str]) -> str:
    metrics = result["metrics"]
    lines = [
        "# 多轮改写评测",
        "",
        "> 由 `scripts.eval_rewrite` 自动生成。改写用的是**人工标注结果**，",
        "> 因此这里衡量的是「改写的收益上限」，不含模型改写本身的质量损耗。",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 样本数 | {metrics['samples']} |",
        f"| 裸追问 recall@5 | {metrics['bare_recall@5']} |",
        f"| 改写后 recall@5 | {metrics['rewritten_recall@5']} |",
        f"| 提升 (lift) | {metrics['lift']} |",
        f"| 门控准确率 | {metrics['gate_accuracy']} |",
        "",
        "## 逐条",
        "",
        "| 追问 | 标注改写 | 裸召回 | 改写后 | 门控 | 门控正确 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in result["details"]:
        gate = "改写" if item["gate"] else "跳过"
        lines.append(
            f"| {item['query']} | {item['rewritten']} | {item['bare_recall@5']} | "
            f"{item['rewritten_recall@5']} | {gate} | {'✓' if item['gate_ok'] else '✗'} |"
        )
    lines += ["", "## 门禁", ""]
    lines.append("通过。" if not problems else "未通过：\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="CI 用：不达标则非零退出")
    args = parser.parse_args()

    result = await run()
    metrics = result["metrics"]
    print(
        f"bare={metrics['bare_recall@5']} rewritten={metrics['rewritten_recall@5']} "
        f"lift={metrics['lift']} gate={metrics['gate_accuracy']} samples={metrics['samples']}"
    )

    problems: list[str] = []
    if metrics["lift"] <= 0:
        problems.append(f"改写没有带来收益（lift={metrics['lift']}），应当关闭该功能")
    if metrics["gate_accuracy"] < MIN_GATE_ACCURACY:
        problems.append(f"门控准确率不足：{metrics['gate_accuracy']} < {MIN_GATE_ACCURACY}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(_render(result, problems), encoding="utf-8")
    print(f"report written: {REPORT}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        if args.check:
            await dispose_engines()
            raise SystemExit(1)

    await dispose_engines()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
