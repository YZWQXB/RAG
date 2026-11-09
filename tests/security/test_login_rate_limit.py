"""登录接口的 IP 维度限流。

账号锁定只拦单个账号被撞；分布式/多账号撞库需要按来源 IP 限速,
否则攻击者换一个邮箱就能重新获得 N 次尝试机会。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db


async def test_login_rate_limited_per_client_ip(anon_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 5)

    statuses = []
    for index in range(7):
        response = await anon_client.post(
            "/api/v1/auth/login",
            json={
                "email": f"probe-{uuid.uuid4().hex[:8]}-{index}@example.com",
                "password": "WrongPass1",
            },
        )
        statuses.append(response.status_code)

    assert 429 in statuses, f"超出限流应返回 429，实际 {statuses}"
    limited = statuses.index(429)
    assert all(code == 429 for code in statuses[limited:]), "限流后应持续拒绝"

    response = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": f"another-{uuid.uuid4().hex[:8]}@example.com", "password": "WrongPass1"},
    )
    assert response.status_code == 429
    assert response.headers.get("retry-after")


async def test_register_is_not_limited_by_login_bucket(anon_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 1)

    response = await anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"reg-{uuid.uuid4().hex[:8]}@example.com",
            "password": "Passw0rd!",
            "tenant_name": "空间",
        },
    )
    assert response.status_code == 201
