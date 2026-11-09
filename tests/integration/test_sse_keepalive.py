"""SSE 心跳。

上游模型首 token 慢、或生成中途卡住时，中间层（nginx / 企业代理）会按空闲超时
切断连接，用户看到的是"回答突然消失"。心跳注释帧是标准解法。
"""

from __future__ import annotations

import pytest

from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db


async def test_stream_emits_keepalive_frames(client_a, tenant_a, monkeypatch):
    from app.core.config import settings
    from app.providers import set_llm_provider
    from app.providers.llm.fake import FakeLLM

    monkeypatch.setattr(settings, "sse_keepalive_seconds", 0.05)
    monkeypatch.setattr(settings, "retrieval_no_answer_threshold", 0.05)
    # 每个字符 0.3s，必然产生多个心跳窗口
    set_llm_provider(FakeLLM(answer="年假五天", delay=0.3))

    kb_id, _, _ = await seed_document(
        tenant_a[0], tenant_a[1], [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )

    raw_lines: list[str] = []
    async with client_a.stream(
        "POST", "/api/v1/chat/stream", json={"kb_ids": [str(kb_id)], "query": "年假"}
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            raw_lines.append(line)

    assert any(line.startswith(": ping") for line in raw_lines), "长间隔生成期间必须发送心跳注释帧"
    assert any(line.startswith("event: token") for line in raw_lines)
