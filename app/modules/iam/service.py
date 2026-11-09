from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, UnauthorizedError
from app.modules.iam.models import Membership, User
from app.modules.iam.schemas import TenantBrief

_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w\-]+\.[\w.]{2,}$")
LOCK_THRESHOLD = 5
LOCK_MINUTES = 10


def validate_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise BadRequestError("邮箱格式不正确", code="INVALID_EMAIL")
    return email


def validate_password(password: str) -> str:
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise BadRequestError("密码至少 8 位，且需同时包含字母和数字", code="WEAK_PASSWORD")
    return password


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def list_memberships(session: AsyncSession, user_id: uuid.UUID) -> list[Membership]:
    result = await session.execute(select(Membership).where(Membership.user_id == user_id))
    return list(result.scalars().all())


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    from app.core.security import verify_password

    email = email.strip().lower()
    user = await get_user_by_email(session, email)
    # 统一文案：不区分账号不存在与密码错误，避免账号枚举
    if user is None:
        raise UnauthorizedError("邮箱或密码错误")

    now = datetime.now(UTC)
    if user.locked_until is not None and user.locked_until > now:
        raise UnauthorizedError("尝试次数过多，请稍后再试")

    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= LOCK_THRESHOLD:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_login_count = 0
        # 必须显式提交：随后抛出的异常会让外层事务回滚，
        # 只 flush 不 commit 的话失败计数永远不会生效，锁定形同虚设。
        await session.commit()
        raise UnauthorizedError("邮箱或密码错误")

    user.failed_login_count = 0
    user.locked_until = None
    await session.flush()
    return user


async def require_membership(
    session: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> Membership:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise UnauthorizedError("你不是该空间的成员")
    return membership


async def tenant_briefs(session: AsyncSession, memberships: list[Membership]) -> list[TenantBrief]:
    from app.modules.tenancy.models import Tenant

    if not memberships:
        return []
    ids = [m.tenant_id for m in memberships]
    result = await session.execute(select(Tenant).where(Tenant.id.in_(ids)))
    tenants = {t.id: t for t in result.scalars().all()}
    briefs = []
    for m in memberships:
        tenant = tenants.get(m.tenant_id)
        if tenant is not None:
            briefs.append(
                TenantBrief(id=tenant.id, slug=tenant.slug, name=tenant.name, role=m.role)
            )
    return briefs


async def ensure_email_available(session: AsyncSession, email: str) -> None:
    if await get_user_by_email(session, email) is not None:
        raise ConflictError("该邮箱已注册", code="EMAIL_TAKEN")
