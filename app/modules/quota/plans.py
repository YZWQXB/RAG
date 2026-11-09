"""套餐限额。首期只有一个 free 套餐，结构留好以便后续加档。"""

from __future__ import annotations

PLANS: dict[str, dict[str, int]] = {
    "free": {
        "documents": 100,
        "storage_bytes": 500 * 1024**2,
        "queries_per_month": 1000,
        "llm_tokens_per_month": 500_000,
    },
}

METRIC_LABELS = {
    "documents": "文档数量已达上限",
    "storage_bytes": "存储空间已满",
    "queries_per_month": "本月查询次数已用尽",
    "llm_tokens_per_month": "本月生成额度已用尽",
}

MONTHLY_METRICS = {"queries_per_month", "llm_tokens_per_month"}


def limits_for(plan: str) -> dict[str, int]:
    return PLANS.get(plan, PLANS["free"])
