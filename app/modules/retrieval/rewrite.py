"""查询归一化。

首期刻意不做 LLM 改写与多查询扩展：每次提问多一次大模型调用会显著抬高成本与延迟，
而收益缺乏评测数据支撑。保留开关，等评测集有数据后再决定。
"""

from __future__ import annotations

import re

MAX_QUERY_CHARS = 512

_FILLERS = (
    "请问",
    "帮我看看",
    "帮忙看看",
    "我想知道",
    "想问一下",
    "麻烦问一下",
    "你好",
)


def normalize_query(query: str) -> str:
    text = query.strip()
    # 全角转半角（仅常见标点与空格）
    text = text.translate({0x3000: " ", 0xFF1F: "?", 0xFF01: "!", 0xFF0C: ","})
    text = re.sub(r"\s+", " ", text)

    changed = True
    while changed:
        changed = False
        for filler in _FILLERS:
            if text.startswith(filler):
                text = text[len(filler) :].strip()
                changed = True

    text = text.strip("？?。.!！,， ")
    if len(text) > MAX_QUERY_CHARS:
        text = text[:MAX_QUERY_CHARS]
    return text or query.strip()
