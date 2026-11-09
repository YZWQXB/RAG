from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.security import Principal
from app.modules.audit import service as audit
from app.modules.iam.models import Membership, User
from app.modules.knowledge.models import KbAcl
from app.modules.knowledge.schemas import MemberOut

MANAGER_ROLES = frozenset({"owner", "admin"})


def _require_manager(principal: Principal) -> None:
    if principal.role not in MANAGER_ROLES:
        raise PermissionDeniedError("只有空间所有者或管理员可以管理成员")


async def list_members(session: AsyncSession, principal: Principal) -> list[MemberOut]:
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .order_by(Membership.created_at)
        )
    ).all()
    return [
        MemberOut(user_id=user.id, email=user.email, name=user.name, role=membership.role)
        for membership, user in rows
    ]


async def add_member(
    session: AsyncSession, principal: Principal, email: str, role: str
) -> MemberOut:
    _require_manager(principal)

    user = (
        await session.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("该邮箱未注册")

    existing = (
        await session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("该用户已在此空间", code="MEMBER_EXISTS")

    session.add(
        Membership(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            user_id=user.id,
            role=role,
        )
    )
    await session.flush()
    await audit.record(
        session,
        principal,
        "member.add",
        target_type="user",
        target_id=user.id,
        detail={"email": user.email, "role": role},
    )
    return MemberOut(user_id=user.id, email=user.email, name=user.name, role=role)


async def update_member_role(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID, role: str
) -> MemberOut:
    _require_manager(principal)
    membership = await _get_membership(session, user_id)
    if membership.role == "owner":
        raise PermissionDeniedError("不能修改空间所有者", code="CANNOT_MODIFY_OWNER")
    previous = membership.role
    membership.role = role
    await session.flush()
    user = await session.get(User, user_id)
    await audit.record(
        session,
        principal,
        "member.role",
        target_type="user",
        target_id=user_id,
        detail={"email": user.email, "from": previous, "to": role},
    )
    return MemberOut(user_id=user.id, email=user.email, name=user.name, role=role)


async def remove_member(session: AsyncSession, principal: Principal, user_id: uuid.UUID) -> None:
    _require_manager(principal)
    membership = await _get_membership(session, user_id)
    if membership.role == "owner":
        raise PermissionDeniedError("不能移除空间所有者", code="CANNOT_MODIFY_OWNER")
    if user_id == principal.user_id:
        raise BadRequestError("不能移除自己", code="CANNOT_REMOVE_SELF")

    # 移除成员时同步清理其在该租户的知识库授权，避免残留权限
    await session.execute(
        delete(KbAcl).where(KbAcl.subject_type == "user", KbAcl.subject_id == str(user_id))
    )
    user = await session.get(User, user_id)
    await session.delete(membership)
    await session.flush()
    await audit.record(
        session,
        principal,
        "member.remove",
        target_type="user",
        target_id=user_id,
        detail={"email": user.email if user else "", "role": membership.role},
    )
    await session.flush()


async def _get_membership(session: AsyncSession, user_id: uuid.UUID) -> Membership:
    membership = (
        await session.execute(select(Membership).where(Membership.user_id == user_id))
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("该成员不存在")
    return membership
