"""FakeLLM 行为约定：默认应从检索上下文派生确定性回答，而不是永远输出同一句硬编码文案。

背景：离线（fake）模式下人工验证时，任何问题都得到「根据资料，年假为 5 天」，
让人误以为检索坏了。实际上检索正常（有测试与诊断脚本佐证），
是假 LLM 忽略了传入的 messages。修正后：显式传 answer 仍完全覆盖
（供需要固定流式输出的用例）；默认从【参考资料】/【问题】派生。
"""

from __future__ import annotations

import pytest

from app.providers.llm.fake import FakeLLM

CONTEXT_MESSAGES = [
    {"role": "system", "content": "系统提示"},
    {
        "role": "user",
        "content": (
            "【参考资料】\n"
            "[1] (文档: C语言.md | 章节: 库函数 | 第 3 页)\n"
            'printf 是格式化输出函数，使用 printf("格式串", 参数) 输出。\n'
            "\n"
            "[2] (文档: Linux复习笔记.md | 章节: man | 第 5 页)\n"
            "man 手册分节。\n"
            "\n"
            "【问题】printf用法是什么"
        ),
    },
]


async def _collect(fake: FakeLLM, messages: list[dict]) -> str:
    parts = [delta.text or "" async for delta in fake.stream(messages)]
    return "".join(parts)


@pytest.mark.asyncio
async def test_answer_quotes_question_and_context():
    fake = FakeLLM()
    text = await _collect(fake, CONTEXT_MESSAGES)

    assert "printf用法是什么" in text, "回答必须包含用户的问题"
    assert "printf" in text.lower(), "回答必须摘录检索到的上下文"
    assert "年假" not in text, "不应再输出与问题无关的硬编码文案"


@pytest.mark.asyncio
async def test_fallback_when_no_context():
    fake = FakeLLM()
    text = await _collect(fake, [{"role": "user", "content": "你好"}])

    assert text == "根据资料，年假为 5 天[1]。", "解析不到参考资料时回退到默认文案"


@pytest.mark.asyncio
async def test_explicit_answer_still_wins():
    """显式传入 answer 的用例（如 SSE 心跳测试）需要固定输出，不受上下文影响。"""
    fake = FakeLLM(answer="固定回答")
    text = await _collect(fake, CONTEXT_MESSAGES)

    assert text == "固定回答"


@pytest.mark.asyncio
async def test_last_messages_recorded():
    fake = FakeLLM()
    await _collect(fake, CONTEXT_MESSAGES)
    assert fake.calls == 1
    assert fake.last_messages == CONTEXT_MESSAGES
