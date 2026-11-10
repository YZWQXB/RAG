"""OpenAI 兼容的流式对话接口（DeepSeek / 通义 / 混元 等）。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings
from app.core.http import get_http_client
from app.core.logging import get_logger
from app.providers.llm.base import LLMDelta, LLMError

log = get_logger("llm")


class OpenAICompatLLM:
    def __init__(self) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model

    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[LLMDelta]:
        if not self.api_key:
            raise LLMError("未配置 LLM_API_KEY")

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": kwargs.get("temperature", 0.2),
        }
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
        try:
            client = get_http_client()
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise LLMError(f"上游返回 {response.status_code}")
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    finish = choices[0].get("finish_reason")
                    if text or finish:
                        yield LLMDelta(text=text, finish_reason=finish)
        except httpx.TimeoutException as exc:
            raise LLMError("生成超时") from exc
        except httpx.HTTPError as exc:
            raise LLMError("生成服务连接失败") from exc
