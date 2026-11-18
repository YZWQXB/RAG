"""多轮指代消解（查询改写）。

背景：检索引擎只看到裸 query。用户第二轮问「它有什么限制」，检索不到任何东西，
因为「它」在索引里不存在。改写的价值全在"接得上上下文"，风险却在"多一次调用、
多一份延迟、可能改错"。所以设计上三条硬约束：

1. 门控：只有「短 + 含指代」才改写，长句自带主语，改了反而丢信息
2. 兜底：超时、报错、模型胡言乱语，一律退回原句——宁可检索不到，不能检索错
3. 缓存：同一 (问句 + 上下文) 只改一次，避免多轮重复问时反复烧 token
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.modules.retrieval import rewrite
from app.providers.llm.base import LLMDelta, LLMError

HISTORY = [
    {"role": "user", "content": "设备的保修期是多久"},
    {"role": "assistant", "content": "整机保修期为 12 个月，主要部件为 24 个月 [1]。"},
]


class _StubLLM:
    """可编程的 LLM 替身。"""

    def __init__(
        self,
        text: str = "",
        *,
        error: Exception | None = None,
        delay: float = 0.0,
        chunks: int = 1,
    ) -> None:
        self.text = text
        self.error = error
        self.delay = delay
        self.chunks = chunks
        self.calls = 0
        self.last_messages: list[dict] = []

    async def stream(self, messages: list[dict], **kwargs):
        self.calls += 1
        self.last_messages = messages
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        step = max(1, len(self.text) // max(1, self.chunks))
        for start in range(0, len(self.text), step):
            yield LLMDelta(text=self.text[start : start + step])


class _FakeRedis:
    """只实现改写用到的三个命令，命中行为与真实 Redis 一致。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.data[key] = value


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rewrite, "get_redis", lambda: fake)
    yield fake


# ---------------- 门控 ----------------


def test_short_anaphora_query_needs_rewrite():
    assert rewrite.needs_rewrite("它有什么限制", HISTORY) is True
    assert rewrite.needs_rewrite("那保修呢", HISTORY) is True


def test_long_query_is_not_rewritten():
    """长句自带主语，改写只会引入偏差。"""
    assert rewrite.needs_rewrite("设备的整机保修期和主要部件保修期分别是多久", HISTORY) is False


def test_first_turn_never_rewritten():
    assert rewrite.needs_rewrite("它有什么限制", []) is False


def test_self_contained_query_is_not_rewritten():
    """没有指代词就不改写，哪怕很短。"""
    assert rewrite.needs_rewrite("保修期多久", HISTORY) is False


# ---------------- 改写与解析 ----------------


async def test_rewrite_strips_quotes_and_prefix():
    llm = _StubLLM("「设备的保修期是多久」")
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm) == "设备的保修期是多久"


async def test_rewrite_rejects_model_refusal():
    """模型答非所问时必须退回原句，不能把"抱歉"当成查询词。"""
    llm = _StubLLM("抱歉，我无法完成这个改写。")
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm) == "它保修多久"


async def test_rewrite_rejects_overlong_output():
    llm = _StubLLM("保修期" * 80)
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm) == "它保修多久"


async def test_rewrite_returns_original_on_llm_error():
    llm = _StubLLM(error=LLMError("上游 500"))
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm) == "它保修多久"


async def test_rewrite_returns_original_on_timeout(monkeypatch):
    monkeypatch.setattr(settings, "query_rewrite_timeout_seconds", 0.05)
    llm = _StubLLM("设备的保修期是多久", delay=0.3)
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm) == "它保修多久"


async def test_rewrite_handles_multi_chunk_stream():
    llm = _StubLLM("设备的保修期是多久", chunks=4)
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm) == "设备的保修期是多久"


# ---------------- 缓存 ----------------


async def test_second_call_hits_cache(_clear_cache):
    llm = _StubLLM("设备的保修期是多久")
    await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm)
    await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm)
    assert llm.calls == 1, "同一问句 + 同一上下文不应重复调用模型"


async def test_cache_key_does_not_contain_plaintext(_clear_cache):
    """Redis 里不应出现用户原文。"""
    llm = _StubLLM("设备的保修期是多久")
    await rewrite.rewrite_query("它保修多久", HISTORY, llm=llm)
    assert all("保修" not in key for key in _clear_cache.data)


async def test_failure_is_not_cached(_clear_cache):
    """失败结果不缓存：上游恢复后应当能改成功。"""
    failing = _StubLLM(error=LLMError("上游 500"))
    await rewrite.rewrite_query("它保修多久", HISTORY, llm=failing)

    healthy = _StubLLM("设备的保修期是多久")
    assert await rewrite.rewrite_query("它保修多久", HISTORY, llm=healthy) == "设备的保修期是多久"


# ---------------- 总入口 ----------------


async def test_maybe_rewrite_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_llm_rewrite", False)
    llm = _StubLLM("设备的保修期是多久")
    query, changed = await rewrite.maybe_rewrite("它保修多久", HISTORY, llm=llm)
    assert (query, changed) == ("它保修多久", False)
    assert llm.calls == 0


async def test_maybe_rewrite_skips_offline_mode(monkeypatch):
    """离线模式没有可用的模型，不做改写——这是它 100% 拒答之外的第二个坑。"""
    monkeypatch.setattr(settings, "enable_llm_rewrite", True)
    monkeypatch.setattr(settings, "llm_provider", "fake")
    llm = _StubLLM("设备的保修期是多久")
    query, changed = await rewrite.maybe_rewrite("它保修多久", HISTORY, llm=llm)
    assert (query, changed) == ("它保修多久", False)
    assert llm.calls == 0


async def test_maybe_rewrite_reports_change(monkeypatch):
    monkeypatch.setattr(settings, "enable_llm_rewrite", True)
    monkeypatch.setattr(settings, "llm_provider", "api")
    llm = _StubLLM("设备的保修期是多久")
    query, changed = await rewrite.maybe_rewrite("它保修多久", HISTORY, llm=llm)
    assert query == "设备的保修期是多久"
    assert changed is True


async def test_maybe_rewrite_no_change_when_model_returns_same(monkeypatch):
    monkeypatch.setattr(settings, "enable_llm_rewrite", True)
    monkeypatch.setattr(settings, "llm_provider", "api")
    llm = _StubLLM("它保修多久")
    query, changed = await rewrite.maybe_rewrite("它保修多久", HISTORY, llm=llm)
    assert query == "它保修多久"
    assert changed is False, "改写结果与原句相同，不应标记为已改写"
