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


@pytest.mark.asyncio
async def test_offline_answer_follows_documented_format():
    """离线回答的固定格式：标题行 + 项目符号摘录[编号] + 离线声明。"""
    fake = FakeLLM()
    text = await _collect(fake, CONTEXT_MESSAGES)

    assert text.startswith("根据资料，「printf用法是什么」的参考内容如下：")
    assert "\n- " in text, "摘录应以项目符号列出"
    assert "[1]" in text, "引用编号必须沿用参考资料里的真实编号"
    assert text.rstrip().endswith("> 离线模式：以上为知识库原文摘录，未经模型归纳生成。")


@pytest.mark.asyncio
async def test_irrelevant_reference_is_filtered():
    """与问题无关的参考块不应出现在离线回答里（避免"凑数"感）。"""
    messages = [
        {
            "role": "user",
            "content": (
                "【参考资料】\n"
                "[1] (文档: C.md | 章节: 递归 | 第 1 页)\n递归是函数调用自身。\n"
                "[2] (文档: DNS.md | 章节: 域名 | 第 9 页)\n根域名服务器保存顶级域地址。\n"
                "\n【问题】递归是什么"
            ),
        }
    ]
    text = await _collect(FakeLLM(), messages)

    assert "递归" in text
    assert "域名" not in text, "无关资料应被过滤"
    assert "[2]" not in text


@pytest.mark.asyncio
async def test_excerpt_strips_chunk_header():
    """分块自带的「[文档: … | 章节: …]」头部不应出现在回答正文里。"""
    messages = [
        {
            "role": "user",
            "content": (
                "【参考资料】\n"
                "[1] (文档: Linux.md | 章节: 文件系统 | 第 2 页)\n"
                "[文档: Linux.md | 章节: 文件系统] ext4 支持日志，xfs 适合大文件。\n"
                "\n【问题】Linux 有哪些文件系统"
            ),
        }
    ]
    text = await _collect(FakeLLM(), messages)

    assert "ext4" in text
    assert "文档:" not in text, "元数据头部应被剥离"
    assert "[1]" in text


@pytest.mark.asyncio
async def test_at_most_three_references():
    blocks = "\n\n".join(
        f"[{i}] (文档: d{i}.md | 章节: h | 第 {i} 页)\n第 {i} 段内容含关键词 递归"
        for i in range(1, 6)
    )
    messages = [{"role": "user", "content": f"【参考资料】\n{blocks}\n\n【问题】递归是什么"}]
    text = await _collect(FakeLLM(), messages)

    assert text.count("- ") == 3, "最多展示 3 条参考资料"
    assert "[4]" not in text and "[5]" not in text
