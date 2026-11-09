from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMDelta:
    text: str = ""
    finish_reason: str | None = None


class LLMError(Exception):
    """上游生成失败。不包含任何密钥或请求体内容。"""


class LLMProvider(Protocol):
    def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[LLMDelta]: ...
