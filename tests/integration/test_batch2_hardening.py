"""第二批加固的回归用例：中断落库、模式切换审计。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.core.security import Principal
from app.modules.conversation.models import Message
from app.modules.generation.schemas import ChatIn
from app.modules.generation.service import stream_answer
from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db

CHUNK = "员工入职满一年可享受 5 天年假。"


async def test_client_disconnect_persists_partial_answer(client_a, tenant_a, low_threshold):
    """用户点「停止」或直接关页面：已生成的内容必须落库。

    否则刷新后这段回答凭空消失，而 token 已经花掉了。
    """
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(tenant_id, user_id, [(CHUNK, await fake_vector(CHUNK))])

    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")
    generator = stream_answer(principal, ChatIn(kb_ids=[kb_id], query="年假有多少天"))

    saw_token = False
    async for chunk in generator:
        if chunk.startswith("event: token"):
            saw_token = True
            break
    assert saw_token, "假 LLM 应该先吐出 token 再被中断"

    await generator.aclose()

    async with session_scope(tenant_id=tenant_id) as session:
        message = (
            await session.execute(
                select(Message)
                .where(Message.role == "assistant")
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        ).scalar_one()

    assert message.status == "incomplete"
    assert message.content, "已生成的部分内容应被保留"


async def test_mode_switch_writes_audit(client_a, monkeypatch):
    """把实例切到离线属于重大配置变更，必须留痕。"""
    from dataclasses import replace

    from app.core import mode as mode_module

    status = mode_module.describe_mode()

    async def fake_apply(key: str, **_kwargs):
        return replace(status, mode=key)

    monkeypatch.setattr(mode_module, "apply_mode", fake_apply)

    response = await client_a.post("/api/v1/system/mode", json={"mode": "offline"})
    assert response.status_code == 200

    logs = await client_a.get("/api/v1/audit-logs", params={"action": "mode.switch"})
    assert logs.status_code == 200
    rows = logs.json()
    assert rows, "切换模式应写入审计日志"
    assert rows[0]["action"] == "mode.switch"
    assert rows[0]["detail"]["to"] == "offline"


async def test_viewer_cannot_switch_mode_or_write_audit(viewer_client_a, monkeypatch):
    from app.core import mode as mode_module

    async def fake_apply(key: str, **_kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("viewer 不应触发模式切换")

    monkeypatch.setattr(mode_module, "apply_mode", fake_apply)

    response = await viewer_client_a.post("/api/v1/system/mode", json={"mode": "offline"})
    assert response.status_code == 403
