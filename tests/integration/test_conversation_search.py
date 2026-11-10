"""会话检索与排序、删除。

列表的三个动作都由服务端完成而不是前端过滤：
会话会越积越多，全量拉回来再筛会让打开页面越来越慢。
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import fake_vector, seed_document

pytestmark = pytest.mark.db


async def _kb(client_a, tenant_a) -> str:
    tenant_id, user_id, _ = tenant_a
    kb_id, _, _ = await seed_document(
        tenant_id, user_id, [("年假 5 天。", await fake_vector("年假 5 天。"))]
    )
    return str(kb_id)


async def _create(client, kb_id: str, title: str) -> dict:
    response = await client.post("/api/v1/conversations", json={"kb_ids": [kb_id], "title": title})
    assert response.status_code == 201
    return response.json()


async def test_search_filters_by_title(client_a, tenant_a):
    kb_id = await _kb(client_a, tenant_a)
    await _create(client_a, kb_id, "年假政策咨询")
    await _create(client_a, kb_id, "报销流程")
    await _create(client_a, kb_id, "Linux 文件系统")

    response = await client_a.get("/api/v1/conversations", params={"q": "年假"})
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert titles == ["年假政策咨询"]


async def test_search_is_case_insensitive(client_a, tenant_a):
    kb_id = await _kb(client_a, tenant_a)
    await _create(client_a, kb_id, "Linux 文件系统")

    response = await client_a.get("/api/v1/conversations", params={"q": "linux"})
    assert [item["title"] for item in response.json()] == ["Linux 文件系统"]


async def test_search_without_match_returns_empty(client_a, tenant_a):
    kb_id = await _kb(client_a, tenant_a)
    await _create(client_a, kb_id, "年假政策咨询")

    response = await client_a.get("/api/v1/conversations", params={"q": "不存在的关键词"})
    assert response.json() == []


async def test_search_escapes_like_wildcards(client_a, tenant_a):
    """用户输入的 % 应作为普通字符，不能变成通配符。"""
    kb_id = await _kb(client_a, tenant_a)
    await _create(client_a, kb_id, "折扣 50% 的活动")
    await _create(client_a, kb_id, "完全无关的会话")

    response = await client_a.get("/api/v1/conversations", params={"q": "50%"})
    assert [item["title"] for item in response.json()] == ["折扣 50% 的活动"]


async def test_order_asc_and_desc(client_a, tenant_a):
    kb_id = await _kb(client_a, tenant_a)
    first = await _create(client_a, kb_id, "最旧")
    second = await _create(client_a, kb_id, "中间")
    third = await _create(client_a, kb_id, "最新")

    # 让 updated_at 拉开差距：给先建的会话发一条消息不会改变顺序表，
    # 这里直接改库更可控
    import datetime as dt

    from sqlalchemy import text

    from app.core.db import session_scope

    order = {first["id"]: 1, second["id"]: 2, third["id"]: 3}
    async with session_scope(tenant_id=tenant_a[0]) as session:
        base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        for conversation_id, offset in order.items():
            await session.execute(
                text("UPDATE conversations SET updated_at = :t WHERE id = :i"),
                {"t": base + dt.timedelta(days=offset), "i": conversation_id},
            )

    desc = await client_a.get("/api/v1/conversations", params={"order": "desc"})
    assert [item["title"] for item in desc.json()] == ["最新", "中间", "最旧"]

    asc = await client_a.get("/api/v1/conversations", params={"order": "asc"})
    assert [item["title"] for item in asc.json()] == ["最旧", "中间", "最新"]


async def test_invalid_order_rejected(client_a):
    response = await client_a.get("/api/v1/conversations", params={"order": "random"})
    assert response.status_code == 400


async def test_delete_hides_conversation(client_a, tenant_a):
    kb_id = await _kb(client_a, tenant_a)
    created = await _create(client_a, kb_id, "待删除")

    deleted = await client_a.delete(f"/api/v1/conversations/{created['id']}")
    assert deleted.status_code == 204

    remaining = await client_a.get("/api/v1/conversations")
    assert created["id"] not in [item["id"] for item in remaining.json()]

    # 再删一次应 404（已软删除）
    again = await client_a.delete(f"/api/v1/conversations/{created['id']}")
    assert again.status_code == 404


async def test_cannot_delete_other_users_conversation(client_a, tenant_a, viewer_client_a):
    kb_id = await _kb(client_a, tenant_a)
    created = await _create(client_a, kb_id, "别人的会话")

    response = await viewer_client_a.delete(f"/api/v1/conversations/{created['id']}")
    assert response.status_code == 404
    assert uuid.UUID(created["id"])  # 仍存在于库中，只是别人删不掉
