"""确定性假 LLM，供测试与无密钥环境使用。

默认从 messages 里的【参考资料】/【问题】派生回答（确定性，不调用外部服务），
让离线联调时回答能真实反映检索结果；显式传入 answer 时完全覆盖。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from app.providers.llm.base import LLMDelta, LLMError

_DEFAULT_ANSWER = "根据资料，年假为 5 天[1]。"
_EXCERPT_LIMIT = 80


def _derive_answer(messages: list[dict]) -> str:
    """从最后的用户消息里提取问题与第一条参考资料，拼出确定性回答。

    解析失败（消息里没有【参考资料】结构）时回退到 _DEFAULT_ANSWER，
    保证没有上下文的调用路径仍有稳定输出。
    """
    user_content = next(
        (m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    question_match = re.search(r"【问题】(.+)", user_content)
    reference_match = re.search(r"\[1\] \([^\n]*\)\n(.+)", user_content)

    if not question_match or not reference_match:
        return _DEFAULT_ANSWER

    question = question_match.group(1).strip()
    excerpt = reference_match.group(1).strip().splitlines()[0].strip()[:_EXCERPT_LIMIT]
    if not excerpt:
        return _DEFAULT_ANSWER
    return f"根据资料，「{question}」的参考内容：{excerpt}……[1]。"


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
