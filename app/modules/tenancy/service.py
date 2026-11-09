from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.core.errors import BadRequestError, ConflictError
from app.core.security import create_access_token, hash_password
from app.core.tenant import bind_tenant
from app.modules.iam.models import Membership, User
from app.modules.iam.service import (
    ensure_email_available,
    validate_email,
    validate_password,
)
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.schemas import RegisterOut


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "space"


# 允许出现在 URL 路径里的空间标识：小写字母、数字、连字符，且必须以字母或数字开头
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def _resolve_slug(slug: str | None, email: str) -> str:
    """校验或生成空间标识。

    调用方传来的 slug 会进入 URL 路径，必须严格限制字符集——
    否则 `../` 这类值虽然当前没有被拼进文件路径，但迟早会在某个环节被误用。
    """
    if slug is None or not slug.strip():
        return f"{_slugify(email.split('@')[0])}-{secrets.token_hex(2)}"

    candidate = slug.strip().lower()
    if not _SLUG_RE.match(candidate):
        raise BadRequestError(
            "空间标识只能包含小写字母、数字和连字符，3-64 位且以字母或数字开头",
            code="INVALID_SLUG",
        )
    return candidate


async def register_tenant(
    email: str, password: str, tenant_name: str, slug: str | None = None
) -> RegisterOut:
    """注册即创建租户，注册人成为 owner。

    这里的顺序是刻意的：tenants 表带 WITH CHECK 策略，插入时必须有 app.tenant_id。
    所以先生成 tenant_id 并绑定会话，再插三张表——否则插入会被 RLS 拒绝。
    """
    email = validate_email(email)
    validate_password(password)
    name = tenant_name.strip()
    if not name:
        raise BadRequestError("空间名称不能为空", code="INVALID_NAME")

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    final_slug = _resolve_slug(slug, email)

    async with SessionLocal() as session:
        async with session.begin():
            await bind_tenant(session, tenant_id)

            # users 无 RLS，可直接查重
            await ensure_email_available(session, email)

            tenant = Tenant(id=tenant_id, slug=final_slug, name=name)
            user = User(
                id=user_id,
                email=email,
                password_hash=hash_password(password),
                name=email.split("@")[0][:64],
            )
            session.add_all([tenant, user])
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError("该空间标识已被占用", code="SLUG_TAKEN") from exc

            session.add(Membership(tenant_id=tenant_id, user_id=user_id, role="owner"))
            await session.flush()

    token = create_access_token(user_id, tenant_id, "owner")
    return RegisterOut(
        token=token,
        tenant_id=tenant_id,
        tenant_name=name,
        tenant_slug=final_slug,
        user_id=user_id,
    )
