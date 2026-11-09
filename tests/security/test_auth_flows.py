"""登录 / 换票 / 锁定 的完整流程。

登录是全系统唯一的匿名入口，也是暴力破解与账号枚举的主要面，
之前只有注册被测过，登录链路是空白。
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from app.core.db import admin_session

pytestmark = pytest.mark.db

PASSWORD = "Passw0rd!"


async def _register(client, email: str) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "tenant_name": "主空间"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_register_then_login_returns_token(anon_client):
    email = f"flow-{uuid.uuid4().hex[:8]}@example.com"
    await _register(anon_client, email)

    response = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selection_required"] is False
    assert body["token"]
    assert body["tenant"]["role"] == "owner"

    me = await anon_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert me.status_code == 200


async def test_wrong_password_and_unknown_email_share_message(anon_client):
    email = f"flow-{uuid.uuid4().hex[:8]}@example.com"
    await _register(anon_client, email)

    wrong = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "WrongPass1"}
    )
    unknown = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD},
    )

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"], (
        "两种失败必须同文案，否则可用于枚举已注册邮箱"
    )


async def test_account_locks_after_five_failures(anon_client):
    email = f"lock-{uuid.uuid4().hex[:8]}@example.com"
    await _register(anon_client, email)

    for _ in range(5):
        response = await anon_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "WrongPass1"}
        )
        assert response.status_code == 401

    # 锁定后即使密码正确也应被拒
    locked = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert locked.status_code == 401
    assert "尝试次数过多" in locked.json()["error"]["message"]


async def test_login_requires_existing_membership(anon_client):
    email = f"orphan-{uuid.uuid4().hex[:8]}@example.com"
    await _register(anon_client, email)

    async with admin_session() as session:
        await session.execute(
            text("DELETE FROM memberships WHERE user_id = (SELECT id FROM users WHERE email = :e)"),
            {"e": email},
        )

    response = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 403


async def test_multi_tenant_login_then_switch(anon_client):
    email = f"multi-{uuid.uuid4().hex[:8]}@example.com"
    first = await _register(anon_client, email)

    second_tenant = uuid.uuid4()
    async with admin_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)"),
            {"id": second_tenant, "slug": f"second-{second_tenant.hex[:8]}", "name": "第二空间"},
        )
        await session.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, role) "
                "VALUES (:id, :tid, :uid, 'editor')"
            ),
            {"id": uuid.uuid4(), "tid": second_tenant, "uid": first["user_id"]},
        )

    login = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    body = login.json()
    assert body["selection_required"] is True
    assert {tenant["id"] for tenant in body["tenants"]} == {
        first["tenant_id"],
        str(second_tenant),
    }

    chosen = await anon_client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": PASSWORD, "tenant_id": str(second_tenant)},
    )
    assert chosen.status_code == 200
    assert chosen.json()["tenant"]["id"] == str(second_tenant)
    assert chosen.json()["tenant"]["role"] == "editor"


async def test_token_exchange_rejects_foreign_tenant(anon_client):
    email = f"foreign-{uuid.uuid4().hex[:8]}@example.com"
    await _register(anon_client, email)

    async with admin_session() as session:
        foreign = uuid.uuid4()
        await session.execute(
            text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, '别家')"),
            {"id": foreign, "slug": f"foreign-{foreign.hex[:8]}"},
        )

    response = await anon_client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": PASSWORD, "tenant_id": str(foreign)},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "slug",
    [
        "../../etc/passwd",  # 路径穿越：虽然当前没拼进文件路径，但迟早会被误用
        "a b",  # 空格会破坏 URL 与日志
        "-leading",  # 必须以字母或数字开头
        "ab",  # 少于 3 位
        "x" * 65,  # 超过 64 位
        "中文空间",  # 非 ASCII
    ],
)
async def test_register_rejects_unsafe_slug(anon_client, slug: str):
    response = await anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"slug-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            "tenant_name": "空间",
            "slug": slug,
        },
    )
    assert response.status_code == 400, response.text
    # 超长 slug 会先被 schema 的 max_length 拦下（VALIDATION_ERROR），
    # 其余非法字符由服务层白名单拦下；两者都必须拒绝。
    assert response.json()["error"]["code"] in {"INVALID_SLUG", "VALIDATION_ERROR"}


async def test_register_normalizes_slug_case(anon_client):
    slug = f"Team-{uuid.uuid4().hex[:8]}"
    response = await anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"slug-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            "tenant_name": "空间",
            "slug": slug,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["tenant_slug"] == slug.lower()


async def test_register_generates_slug_when_omitted(anon_client):
    response = await anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"auto-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            "tenant_name": "自动空间",
        },
    )
    assert response.status_code == 201, response.text
    generated = response.json()["tenant_slug"]
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", generated), generated
