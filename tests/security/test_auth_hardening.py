"""认证与会话的加固回归。

这组用例针对的是"令牌自带身份、服务端不复核"这一类问题：
token 里有 role 和 tenant，如果服务端只信 token，就会出现
「成员被移除后仍可访问」「降权后仍按旧角色操作」「同租户内可读他人会话」。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.helpers import client_with_token, create_member_token

pytestmark = pytest.mark.db


async def _delete_membership(tenant_id, user_id) -> None:
    from app.core.db import admin_session

    async with admin_session() as session:
        await session.execute(
            text("DELETE FROM memberships WHERE tenant_id = :t AND user_id = :u"),
            {"t": tenant_id, "u": user_id},
        )


async def test_removed_member_token_is_rejected(tenant_a):
    tenant_id = tenant_a[0]
    user_id, token = await create_member_token(tenant_id, "editor", "removed@example.com")

    async with client_with_token(token) as client:
        assert (await client.get("/api/v1/auth/me")).status_code == 200

        await _delete_membership(tenant_id, user_id)

        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401, "移除成员后旧令牌必须立即失效"
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_role_downgrade_takes_effect_immediately(tenant_a):
    tenant_id = tenant_a[0]
    user_id, token = await create_member_token(tenant_id, "admin", "downgraded@example.com")

    async with client_with_token(token) as client:
        assert (await client.post("/api/v1/kbs", json={"name": "降权前"})).status_code == 201

        from app.core.db import admin_session

        async with admin_session() as session:
            await session.execute(
                text("UPDATE memberships SET role = 'viewer' WHERE user_id = :u"),
                {"u": user_id},
            )

        response = await client.post("/api/v1/kbs", json={"name": "降权后"})
        assert response.status_code == 403, "降权后不应继续按旧角色放行"


async def test_conversation_not_readable_by_other_user_in_same_tenant(
    client_a, tenant_a, low_threshold
):
    from tests.helpers import fake_vector, seed_document

    kb_id, _, _ = await seed_document(
        tenant_a[0], tenant_a[1], [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )

    async with client_a.stream(
        "POST", "/api/v1/chat/stream", json={"kb_ids": [str(kb_id)], "query": "年假"}
    ) as response:
        async for _ in response.aiter_lines():
            pass

    conversation_id = (await client_a.get("/api/v1/conversations")).json()[0]["id"]

    _, other_token = await create_member_token(tenant_a[0], "editor", "peeker@example.com")
    async with client_with_token(other_token) as other:
        response = await other.get(f"/api/v1/conversations/{conversation_id}/messages")
        assert response.status_code == 404, "同租户内不得读取他人会话"


async def test_conversation_not_deletable_by_other_user_in_same_tenant(
    client_a, tenant_a, low_threshold
):
    from tests.helpers import fake_vector, seed_document

    kb_id, _, _ = await seed_document(
        tenant_a[0], tenant_a[1], [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )
    async with client_a.stream(
        "POST", "/api/v1/chat/stream", json={"kb_ids": [str(kb_id)], "query": "年假"}
    ) as response:
        async for _ in response.aiter_lines():
            pass
    conversation_id = (await client_a.get("/api/v1/conversations")).json()[0]["id"]

    _, other_token = await create_member_token(tenant_a[0], "admin", "deleter@example.com")
    async with client_with_token(other_token) as other:
        response = await other.delete(f"/api/v1/conversations/{conversation_id}")
        assert response.status_code == 404, "同租户内不得删除他人会话"


@pytest.mark.parametrize(
    "slug",
    ["../../evil", "a/b", "with space", "sl@sh", "-"],
)
async def test_register_rejects_unsafe_slug(anon_client, slug):
    response = await anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"slug-{abs(hash(slug)) % 100000}@example.com",
            "password": "Passw0rd!",
            "tenant_name": "空间",
            "slug": slug,
        },
    )
    assert response.status_code == 400, f"非法 slug 应被拒绝: {slug!r}"


async def test_conversation_list_only_contains_own(client_a, tenant_a, low_threshold):
    """会话列表属于个人数据，同租户其他成员不应看到标题与条数。"""
    from tests.helpers import fake_vector, seed_document

    kb_id, _, _ = await seed_document(
        tenant_a[0], tenant_a[1], [("年假为 5 天。", await fake_vector("年假为 5 天。"))]
    )
    async with client_a.stream(
        "POST", "/api/v1/chat/stream", json={"kb_ids": [str(kb_id)], "query": "年假"}
    ) as response:
        async for _ in response.aiter_lines():
            pass
    assert len((await client_a.get("/api/v1/conversations")).json()) == 1

    _, other_token = await create_member_token(tenant_a[0], "editor", "lister@example.com")
    async with client_with_token(other_token) as other:
        assert (await other.get("/api/v1/conversations")).json() == []
