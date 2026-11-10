"""问答接口的用户维度限流。

与登录限流（按 IP）不同：问答时已经知道用户是谁，按 user_id 计数更精准——
同租户的正常用户不会因为共用出口 IP 互相挤兑，而脚本狂刷者只能刷爆自己的额度。

限流必须在流式响应开始前触发：SSE 一旦开始就无法再改状态码，
所以这道闸门放在 _precheck 里，与配额检查同层。
"""

from __future__ import annotations

import pytest

from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db


async def _seed(client_a, tenant_a) -> str:
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id,
        user_id,
        [("员工入职满一年可享受 5 天年假。", await fake_vector("员工入职满一年可享受 5 天年假。"))],
    )
    return str(kb_id)


async def _ask(client, kb_id: str) -> int:
    response = await client.post(
        "/api/v1/chat/stream",
        json={"kb_ids": [kb_id], "query": "年假有多少天"},
    )
    return response.status_code


async def test_chat_rate_limited_per_user(client_a, tenant_a, low_threshold, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "chat_rate_limit_per_minute", 3)
    kb_id = await _seed(client_a, tenant_a)

    statuses = [await _ask(client_a, kb_id) for _ in range(5)]

    assert 429 in statuses, f"超出限流应返回 429，实际 {statuses}"
    limited = statuses.index(429)
    assert limited >= 3, "限额内不应被拦"
    assert all(code == 429 for code in statuses[limited:]), "限流后应持续拒绝"

    response = await client_a.post(
        "/api/v1/chat/stream", json={"kb_ids": [kb_id], "query": "年假有多少天"}
    )
    assert response.status_code == 429
    assert response.headers.get("retry-after")


async def test_chat_limit_isolated_per_user(
    client_a, tenant_a, viewer_client_a, low_threshold, monkeypatch
):
    """用户 A 刷爆额度，不影响同租户的用户 B。"""

    import uuid as uuid_mod

    from app.core.config import settings
    from app.core.db import session_scope
    from app.modules.knowledge.models import KbAcl

    monkeypatch.setattr(settings, "chat_rate_limit_per_minute", 2)
    kb_id = await _seed(client_a, tenant_a)

    # viewer 默认对库无权限，补一条角色授权，把权限因素从用例里排除
    async with session_scope(tenant_id=tenant_a[0]) as session:
        session.add(
            KbAcl(
                tenant_id=tenant_a[0],
                kb_id=uuid_mod.UUID(kb_id),
                subject_type="role",
                subject_id="viewer",
                permission="read",
            )
        )

    for _ in range(4):
        await _ask(client_a, kb_id)
    assert await _ask(client_a, kb_id) == 429

    assert await _ask(viewer_client_a, kb_id) == 200, "另一个用户不应被连坐"


async def test_chat_limit_disabled_when_zero(client_a, tenant_a, low_threshold, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "chat_rate_limit_per_minute", 0)
    kb_id = await _seed(client_a, tenant_a)

    statuses = [await _ask(client_a, kb_id) for _ in range(8)]
    assert 429 not in statuses, "limit<=0 表示关闭限流，全部放行"
