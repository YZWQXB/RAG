from __future__ import annotations

import uuid

import jwt
import pytest

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import (
    Principal,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("Passw0rd!")
    assert hashed != "Passw0rd!"
    assert verify_password("Passw0rd!", hashed)
    assert not verify_password("wrong-pass1", hashed)


def test_password_hash_is_salted():
    assert hash_password("Passw0rd!") != hash_password("Passw0rd!")


def test_token_roundtrip():
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(user_id, tenant_id, "owner")
    principal = decode_token(token)
    assert principal == Principal(user_id=user_id, tenant_id=tenant_id, role="owner")


def test_token_carries_tenant_claim():
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "viewer")
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert "tid" in payload and payload["role"] == "viewer"


def test_expired_token_rejected():
    import time
    from datetime import UTC, datetime, timedelta

    payload = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "role": "owner",
        "iat": int(time.time()) - 7200,
        "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(UnauthorizedError):
        decode_token(token)


def test_garbage_token_rejected():
    with pytest.raises(UnauthorizedError):
        decode_token("not-a-token")
