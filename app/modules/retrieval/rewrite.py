"""查询归一化与多轮指代消解。

## 为什么要改写

检索层只看到一条裸 query。用户第二轮问「它有什么限制」时，「它」不在任何分块的
文本里，召回必然落空——这不是阈值问题，是查询本身信息不足。

## 为什么不是每轮都改写

改写要额外一次 LLM 调用，直接抬高首字延迟。所以设了三道闸：

1. **门控**：只在「问句短 + 含指代/省略」时改写。长句通常自带主语，
   改了反而可能引入偏差；首轮没有上下文，改了也没有依据。
2. **超时**：`QUERY_REWRITE_TIMEOUT_SECONDS`（默认 10 秒）到点就放弃改写，
   用原句继续检索。改写是优化项，不能变成可用性风险。
   超时给得比较宽，是因为**改写只在原句检索失败之后才触发**——此时用户本来
   就要收到「资料未涵盖」，多等几秒换一个能答的结果是划算的；常规提问因此零额外延迟。
3. **缓存**：同一 (问句 + 上下文) 只改一次，多轮里反复追问不重复烧 token。
   缓存键是哈希，Redis 里不留用户原文。

任何异常（上游 5xx、超时、模型输出「抱歉我无法…」）一律退回原句：
**宁可检索不到，也不能检索错。**

离线模式（`LLM_PROVIDER=fake`）下不做改写——假模型不会改写，只会输出固定句式。
"""

from __future__ import annotations

import asyncio
import hashlib
import re

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import QUERY_REWRITE
from app.core.redis_client import get_redis
from app.providers import get_llm_provider
from app.providers.llm.base import LLMError, LLMProvider

log = get_logger("rewrite")

MAX_QUERY_CHARS = 512

# 改写结果的长度上限：正常改写不会比原句长很多，超了说明模型在自说自话
MAX_REWRITTEN_CHARS = 200
REWRITE_CACHE_TTL = 3600
_CONTEXT_TURNS = 2

_FILLERS = (
    "请问",
    "帮我看看",
    "帮忙看看",
    "我想知道",
    "想问一下",
    "麻烦问一下",
    "你好",
)

# 指代词与省略式追问。命中任一即认为这句话脱离上下文不可检索
_ANAPHORA = (
    "它",
    "他",
    "她",
    "这个",
    "那个",
    "这些",
    "那些",
    "该",
    "此",
    "其",
    "上述",
    "上面",
    "刚才",
    "前面",
    "继续",
    "再说",
    "详细",
    "还有",
    "呢",
)

# 模型跑偏的信号：出现这些词说明它没有在改写，而是在回答或道歉
_REFUSAL_MARKERS = ("抱歉", "对不起", "无法", "不能", "作为", "改写")

_QUOTE_CHARS = "「」『』“”\"'`《》"

_SYSTEM_PROMPT = """你是检索查询改写器。把用户最后一句改写成一条可以独立检索的完整问题。

规则：
1. 只输出改写后的问题本身，不要解释、不要加引号、不要加序号
2. 补齐被省略的主语或宾语，指代对象必须来自给出的对话历史
3. 如果无法确定指代对象，就原样输出原句
4. 保留原语言，不超过 40 字"""


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


def _has_prior_turn(history: list[dict] | None) -> bool:
    return any(item.get("role") == "user" for item in history or [])


def needs_rewrite(query: str, history: list[dict] | None) -> bool:
    """是否需要结合上下文改写。

    三个条件同时成立才改写：有历史、问句够短、含指代或省略线索。
    """
    if not _has_prior_turn(history):
        return False
    text = query.strip()
    if not text or len(text) > settings.query_rewrite_max_chars:
        return False
    return any(token in text for token in _ANAPHORA)


def _context_snippet(history: list[dict] | None) -> str:
    """取最近两轮作为改写依据。取太多会让模型被更早的话题带偏。"""
    turns = [
        item
        for item in (history or [])
        if item.get("role") in {"user", "assistant"} and (item.get("content") or "").strip()
    ][-_CONTEXT_TURNS * 2 :]
    lines = []
    for item in turns:
        speaker = "用户" if item["role"] == "user" else "助手"
        content = re.sub(r"\s+", " ", item["content"]).strip()
        lines.append(f"{speaker}：{content[:300]}")
    return "\n".join(lines)


def _cache_key(query: str, context: str) -> str:
    digest = hashlib.sha256(f"{query}\x00{context}".encode()).hexdigest()
    return f"rw:{digest}"


def _clean_output(raw: str) -> str:
    text = raw.strip()
    # 模型常见的自说自话前缀
    text = re.sub(r"^(改写后|改写|查询|问题|答案)\s*[:：]\s*", "", text)
    return re.sub(r"\s+", " ", text.strip(_QUOTE_CHARS).strip())


def _is_usable(candidate: str, query: str) -> bool:
    if not candidate or len(candidate) > MAX_REWRITTEN_CHARS:
        return False
    if "\n" in candidate:
        return False
    if any(marker in candidate for marker in _REFUSAL_MARKERS):
        return False
    # 改完还是同一句（或只差标点）说明没补出任何信息，不必当成改写成功
    return normalize_query(candidate) != normalize_query(query)


async def _stream_text(
    llm: LLMProvider, messages: list[dict], timeout: float, *, model: str | None = None
) -> str:
    async def _collect() -> str:
        parts: list[str] = []
        size = 0
        kwargs = {"model": model} if model else {}
        async for delta in llm.stream(messages, **kwargs):
            if not delta.text:
                continue
            parts.append(delta.text)
            size += len(delta.text)
            # 模型话痨时提前刹车，避免为一个改写读几千字
            if size > MAX_REWRITTEN_CHARS * 2:
                break
        return "".join(parts)

    return await asyncio.wait_for(_collect(), timeout)


async def rewrite_query(
    query: str,
    history: list[dict] | None,
    *,
    llm: LLMProvider | None = None,
) -> str:
    """把追问改写成可独立检索的问题；任何异常都退回原句。"""
    context = _context_snippet(history)
    key = _cache_key(query, context)

    try:
        cached = await get_redis().get(key)
        if cached:
            return cached
    except Exception as exc:
        log.warning("rewrite_cache_miss_degraded", error=type(exc).__name__)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"对话历史：\n{context}\n\n待改写的问题：{query}",
        },
    ]

    try:
        raw = await _stream_text(
            llm or get_llm_provider(),
            messages,
            settings.query_rewrite_timeout_seconds,
            model=settings.query_rewrite_model or None,
        )
    except TimeoutError:
        log.info("rewrite_timeout", query_len=len(query))
        QUERY_REWRITE.labels(result="timeout").inc()
        return query
    except LLMError as exc:
        log.warning("rewrite_llm_error", error=type(exc).__name__)
        QUERY_REWRITE.labels(result="failed").inc()
        return query
    except Exception as exc:  # 改写是优化项，任何意外都不能挡住问答
        log.warning("rewrite_unexpected_error", error=type(exc).__name__)
        QUERY_REWRITE.labels(result="failed").inc()
        return query

    candidate = _clean_output(raw)
    if not _is_usable(candidate, query):
        log.info("rewrite_rejected", length=len(candidate))
        QUERY_REWRITE.labels(result="rejected").inc()
        return query

    try:
        await get_redis().setex(key, REWRITE_CACHE_TTL, candidate)
    except Exception as exc:
        log.warning("rewrite_cache_write_failed", error=type(exc).__name__)

    QUERY_REWRITE.labels(result="applied").inc()
    log.info("rewrite_applied", from_len=len(query), to_len=len(candidate))
    return candidate


async def maybe_rewrite(
    query: str,
    history: list[dict] | None,
    *,
    llm: LLMProvider | None = None,
) -> tuple[str, bool]:
    """总入口：按门控决定是否改写，返回 (检索用查询, 是否真的改了)。

    **调用时机由调用方决定**：本函数只判断"这句追问是否具备改写条件"，
    不做"值不值得等"的决策。当前策略是「原句检索失败后才调用」，
    让常规提问零额外延迟——见 `generation/service._prepare`。

    离线模式直接跳过：假模型不会改写，调用它只会白等。
    """
    if not settings.enable_llm_rewrite or settings.llm_provider == "fake":
        return query, False
    if not needs_rewrite(query, history):
        return query, False

    rewritten = await rewrite_query(query, history, llm=llm)
    return rewritten, rewritten != query
