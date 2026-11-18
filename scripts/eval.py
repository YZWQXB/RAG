"""检索质量回归。

指标（全部按「类型」分别统计，因为 fact 和 cross 的难度不是一个量级）：

- recall@5       前 5 条候选中命中的标注块比例
- mrr            首个命中排名的倒数，未命中记 0
- context_recall 标注块进入**最终上下文**的比例（与 recall@5 不同：前者是候选层，
                 后者是真正喂给模型的那几块。候选命中但没进上下文，等于白搭）
- no_answer_acc  无答案样本被正确判为"资料不足"的比例
- p50/p95        单次检索耗时

用法：
    uv run python -m scripts.build_eval_corpus     # 首次或语料变更后
    uv run python -m scripts.eval                  # 跑一次并与基线比对
    uv run python -m scripts.eval --check          # CI 用：退化则非零退出
    uv run python -m scripts.eval --write-baseline # 确认改善后更新基线

基线比对只在 `--check` 时生效：日常跑数看一眼就行，不该因为阈值抖动阻断开发。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import settings
from app.core.db import dispose_engines, session_scope
from app.core.security import Principal
from app.modules.retrieval.service import answerable, retrieve

DATASET = Path("tests/eval/dataset.jsonl")
CORPUS = Path("tests/eval/corpus.json")
BASELINE = Path("tests/eval/baseline.json")
LAST_RUN = Path("tests/eval/last_run.json")
REPORT = Path("docs/reports/eval-latest.md")

RECALL_K = 5
REGRESSION_TOLERANCE = 0.02
# 逐条对比的指标；samples 只是规模标记，不参与退化判定
COMPARED_METRICS = ("recall@5", "mrr", "context_recall", "no_answer_accuracy")


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


def _content_of(chunk_id: str, corpus: list[dict]) -> str:
    for entry in corpus:
        if entry["chunk_id"] == chunk_id:
            return entry["content"]
    return ""


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


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


def _empty_bucket() -> dict:
    return {"count": 0, "recall@5": 0.0, "mrr": 0.0, "context_recall": 0.0}


async def run() -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    dataset = _load_dataset()
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

    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    context_recalls: list[float] = []
    latencies: list[int] = []
    no_answer_hits = 0
    no_answer_total = 0
    unresolved: list[str] = []
    by_type: dict[str, dict] = defaultdict(_empty_bucket)
    details: list[dict] = []

    for item in dataset:
        async with session_scope(tenant_id=tenant_id) as session:
            result = await retrieve(session, principal, kb_ids, item["query"])

        latencies.append(result.timings_ms.get("total", 0))
        retrieved = [str(candidate.id) for candidate in result.candidates]
        top5 = retrieved[:RECALL_K]
        # 与线上同一条判定：稠密或稀疏任一过线即作答。
        # 之前这里只看稠密分，导致评测结论和线上行为不一致。
        predicted_no_answer = not result.context.blocks or not answerable(
            result.top_dense_score, result.top_sparse_score
        )
        bucket = by_type[item["type"]]
        bucket["count"] += 1

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
            # 标注解析不到块说明语料重建过、或标注写错了。
            # 静默跳过会让指标凭空变好，这里必须炸出来。
            unresolved.append(item["query"])
            continue

        hit = len(relevant & set(top5)) / len(relevant)
        recalls.append(hit)
        bucket["recall@5"] += hit

        # 真正喂给模型的块里，标注块占多少。
        # 不能只比 chunk_id：assemble_context 会把相邻块合并成一组，组的 chunk_id
        # 只记首块，被合并进来的标注块会被漏算（实测把 0.53 算成了 0.90+）。
        # 上下文是原文拼接，按内容包含判断才准。
        context_text = "\n".join(block.content for block in result.context.blocks)
        in_context = {
            chunk_id for chunk_id in relevant if _content_of(chunk_id, corpus) in context_text
        }
        context_hit = len(in_context) / len(relevant)
        context_recalls.append(context_hit)
        bucket["context_recall"] += context_hit

        rank = next(
            (index for index, chunk_id in enumerate(retrieved, start=1) if chunk_id in relevant),
            0,
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        bucket["mrr"] += 1.0 / rank if rank else 0.0
        details.append(
            {
                "query": item["query"],
                "type": item["type"],
                "recall@5": round(hit, 3),
                "context_recall": round(context_hit, 3),
                "first_hit_rank": rank,
            }
        )

    if unresolved:
        joined = "\n  - ".join(unresolved)
        raise SystemExit(
            f"以下 {len(unresolved)} 条标注在语料里找不到对应块，"
            f"请重建语料或修正标注：\n  - {joined}"
        )

    def _avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    metrics = {
        "recall@5": _avg(recalls),
        "mrr": _avg(reciprocal_ranks),
        "context_recall": _avg(context_recalls),
        "no_answer_accuracy": round(no_answer_hits / no_answer_total, 4)
        if no_answer_total
        else 1.0,
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "samples": len(details),
    }

    type_metrics = {
        name: {
            "count": bucket["count"],
            "recall@5": round(bucket["recall@5"] / bucket["count"], 4),
            "mrr": round(bucket["mrr"] / bucket["count"], 4),
            "context_recall": round(bucket["context_recall"] / bucket["count"], 4),
        }
        for name, bucket in sorted(by_type.items())
        if bucket["count"]
    }

    return {"metrics": metrics, "by_type": type_metrics, "details": details}


def compare(metrics: dict, baseline: dict) -> list[str]:
    problems: list[str] = []
    for key in COMPARED_METRICS:
        base = baseline.get(key)
        if base is None:
            continue
        if metrics[key] < base - REGRESSION_TOLERANCE:
            problems.append(f"{key} 退化：{metrics[key]} < 基线 {base} - {REGRESSION_TOLERANCE}")
    return problems


def _render_report(result: dict, baseline: dict | None, problems: list[str]) -> str:
    metrics = result["metrics"]
    lines = [
        "# 检索评测报告",
        "",
        "> 由 `scripts.eval` 自动生成，勿手工编辑。每次改动检索链路后重跑即可看到对比。",
        "",
        f"- 样本数：{metrics['samples']}",
        f"- 检索耗时：p50 {metrics['latency_p50_ms']} ms / p95 {metrics['latency_p95_ms']} ms",
        f"- 生成环境：embedding=`{settings.embedding_provider}`，"
        f"rerank=`{settings.reranker_provider}`，"
        f"chunk={settings.chunk_size_chars} 字 / overlap {settings.chunk_overlap_chars} 字",
        "",
        "## 总体",
        "",
        "| 指标 | 当前 | 基线 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    descriptions = {
        "recall@5": "前 5 条候选命中标注块的比例",
        "mrr": "首个命中排名的倒数",
        "context_recall": "标注块进入最终上下文的比例",
        "no_answer_accuracy": "无答案问题被正确拒答的比例",
    }
    for key, desc in descriptions.items():
        base = baseline.get(key) if baseline else None
        lines.append(f"| {key} | {metrics[key]} | {base if base is not None else '—'} | {desc} |")

    lines += [
        "",
        "## 分类型",
        "",
        "| 类型 | 条数 | recall@5 | mrr | context_recall |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, bucket in result["by_type"].items():
        lines.append(
            f"| {name} | {bucket['count']} | {bucket['recall@5']} | "
            f"{bucket['mrr']} | {bucket['context_recall']} |"
        )

    misses = [
        item
        for item in result["details"]
        if item.get("first_hit_rank") == 0 or item.get("no_answer") is False
    ]
    lines += ["", "## 未命中", ""]
    if misses:
        for item in misses:
            reason = "该拒答却作答" if item.get("type") == "no_answer" else "前 5 条未召回标注块"
            lines.append(f"- `{item['query']}`（{item['type']}）：{reason}")
    else:
        lines.append("无。")

    lines += ["", "## 门禁", ""]
    lines.append("通过。" if not problems else "未通过：\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--check", action="store_true", help="CI 用：任一指标相对基线退化则非零退出"
    )
    args = parser.parse_args()

    result = await run()
    metrics = result["metrics"]
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"recall@5={metrics['recall@5']} mrr={metrics['mrr']} "
        f"context_recall={metrics['context_recall']} "
        f"no_answer={metrics['no_answer_accuracy']} samples={metrics['samples']} "
        f"p95={metrics['latency_p95_ms']}ms"
    )

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    problems = compare(metrics, baseline) if baseline else []

    if args.write_baseline or baseline is None:
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
        problems = []

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(_render_report(result, baseline, problems), encoding="utf-8")
    print(f"report written: {REPORT}")

    if problems:
        for problem in problems:
            print(f"REGRESSION: {problem}", file=sys.stderr)
        if args.check:
            await dispose_engines()
            raise SystemExit(1)

    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
