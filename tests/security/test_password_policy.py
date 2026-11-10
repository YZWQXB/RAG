"""密码策略与注册限流。

密码此前只校验非空（min_length=1），"1" 即可注册；注册接口也没有限流，
可被脚本批量刷号占库。两条都属于对外可见的安全底线。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db


def _payload(password: str) -> dict:
    return {
        "email": f"pw-{uuid.uuid4().hex[:8]}@example.com",
        "password": password,
        "tenant_name": "密码空间",
    }


@pytest.mark.parametrize("weak", ["1", "1234567", "abcdefgh", "12345678", "aaaaaaaa1"])
async def test_weak_password_rejected(anon_client, weak: str):
    response = await anon_client.post("/api/v1/auth/register", json=_payload(weak))
    assert response.status_code == 400, f"弱口令 {weak!r} 不应通过"
    assert response.json()["error"]["code"] == "WEAK_PASSWORD"


async def test_strong_password_accepted(anon_client):
    response = await anon_client.post("/api/v1/auth/register", json=_payload("Passw0rd!2026"))
    assert response.status_code == 201


async def test_register_rate_limited_per_ip(anon_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "register_rate_limit_per_minute", 3)

    statuses = []
    for index in range(5):
        response = await anon_client.post(
            "/api/v1/auth/register", json=_payload(f"Passw0rd!{index}x")
        )
        statuses.append(response.status_code)

    assert statuses.count(429) >= 1, f"超出注册限流应返回 429，实际 {statuses}"
    limited = await anon_client.post("/api/v1/auth/register", json=_payload("Passw0rd!xx"))
    assert limited.status_code == 429
    assert limited.headers.get("retry-after")


async def test_login_still_works_with_valid_password(anon_client, monkeypatch):
    email = f"pw-{uuid.uuid4().hex[:8]}@example.com"
    created = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Passw0rd!2026", "tenant_name": "登录空间"},
    )
    assert created.status_code == 201

    response = await anon_client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Passw0rd!2026"}
    )
    assert response.status_code == 200
    assert response.json()["token"]
