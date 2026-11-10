"""确定性假 LLM，供测试与无密钥环境使用。

离线回答格式（固定约定，便于用户区分"离线摘录"与"在线生成"）：

    根据资料，「{问题}」的参考内容如下：

    - {资料摘录} [1]
    - {资料摘录} [2]

    > 离线模式：以上为知识库原文摘录，未经模型归纳生成。

- 最多取 3 条参考资料，按检索顺序
- 每条摘录截断到 120 字，超长以 … 结尾
- 引用编号沿用参考资料里的真实编号，前端可正常解析并跳转
- 摘录会去掉分块自带的「[文档: … | 章节: …]」头部，避免正文以元数据开头

显式传入 answer 时完全覆盖（供需要固定流式输出的用例）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from app.providers.llm.base import LLMDelta, LLMError

_DEFAULT_ANSWER = "根据资料，年假为 5 天[1]。"
_EXCERPT_LIMIT = 120
_MAX_BLOCKS = 3

# 与 lexical 打分同一套虚词表：这些字几乎出现在每段文字里，不能用来判断相关性
_STOP = set("的了吗呢是在和有与及什么怎么如何为个我你他它们这那")
_MIN_OVERLAP = 0.2

_REFERENCE_RE = re.compile(
    r"\[(\d{1,2})\] \([^\n]*\)\n(.+?)(?=\n+\[\d{1,2}\] \(|\n+【问题】|$)",
    re.S,
)


def _clean_excerpt(content: str) -> str:
    """把分块正文压成一行可读摘录。

    分块首行通常是「[文档: x | 章节: y]」加表格表头，直接展示会让离线回答
    以元数据开头，因此去掉头部并压平竖线。
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""

    if lines[0].startswith("[文档:"):
        remainder = lines[0].split("]", 1)[1].strip() if "]" in lines[0] else ""
        lines = ([remainder] if remainder else []) + lines[1:]

    if not lines:
        return ""

    text = " ".join(lines[:2])
    text = text.replace("|", " ")
    # 表格分隔行（--- / :---:）是排版噪音，摘录里没有意义
    text = re.sub(r"(?:\s*:?-{3,}:?)+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，。；：:、-")
    if not text:
        return ""
    return text[:_EXCERPT_LIMIT] + ("…" if len(text) > _EXCERPT_LIMIT else "")


def _overlap(question: str, text: str) -> float:
    """问题字符被摘录覆盖的比例，用来剔除明显无关的参考资料。"""
    query_chars = {ch for ch in question if not ch.isspace() and ch not in _STOP}
    if not query_chars:
        return 0.0
    return len(query_chars & set(text)) / len(query_chars)


def _derive_answer(messages: list[dict]) -> str:
    """从最后的用户消息里提取问题与参考资料，拼出确定性回答。

    解析失败（消息里没有【参考资料】结构）时回退到 _DEFAULT_ANSWER，
    保证没有上下文的调用路径仍有稳定输出。
    """
    user_content = next(
        (m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    question_match = re.search(r"【问题】(.+)", user_content)
    if not question_match:
        return _DEFAULT_ANSWER

    question = question_match.group(1).strip()

    candidates: list[tuple[float, str, str]] = []
    for match in _REFERENCE_RE.finditer(user_content):
        index, content = match.group(1), match.group(2)
        excerpt = _clean_excerpt(content)
        if not excerpt:
            continue
        candidates.append((_overlap(question, excerpt), index, excerpt))

    # 检索会返回若干"沾边"的块，但把明显无关的也列出来会让回答显得不专业。
    # 至少保留一条：全部低于阈值时取第一条，避免出现空回答。
    relevant = [item for item in candidates if item[0] >= _MIN_OVERLAP]
    if not relevant and candidates:
        relevant = [max(candidates, key=lambda item: item[0])]

    relevant.sort(key=lambda item: item[0], reverse=True)
    bullets = [f"- {excerpt} [{index}]" for _, index, excerpt in relevant[:_MAX_BLOCKS]]

    if not bullets:
        return _DEFAULT_ANSWER

    return (
        f"根据资料，「{question}」的参考内容如下：\n\n"
        + "\n".join(bullets)
        + "\n\n> 离线模式：以上为知识库原文摘录，未经模型归纳生成。"
    )


class FakeLLM:
    def __init__(
        self,
        answer: str | None = None,
        *,
        raise_after: int | None = None,
        delay: float = 0.0,
    ) -> None:
        self.answer = answer
        self.raise_after = raise_after
        self.delay = delay
        self.calls = 0
        self.last_messages: list[dict] = []

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[LLMDelta]:
        self.calls += 1
        self.last_messages = messages
        text = self.answer if self.answer is not None else _derive_answer(messages)
        tokens = list(text)
        for index, char in enumerate(tokens):
            if self.raise_after is not None and index >= self.raise_after:
                raise LLMError("模拟生成中断")
            if self.delay:
                await asyncio.sleep(self.delay)
            yield LLMDelta(text=char)
        yield LLMDelta(finish_reason="stop")
