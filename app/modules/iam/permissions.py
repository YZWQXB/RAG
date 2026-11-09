"""权限判定的唯一入口。

其它模块（尤其是 router）禁止自写角色比较逻辑，否则新增入口必然漏判。
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.security import Principal
from app.modules.knowledge.models import KbAcl, KnowledgeBase


class Action(StrEnum):
    READ = "read"
    WRITE = "write"
    MANAGE = "manage"


PERMISSION_EXPANSION: dict[str, set[Action]] = {
    "read": {Action.READ},
    "write": {Action.READ, Action.WRITE},
    "manage": {Action.READ, Action.WRITE, Action.MANAGE},
}

FULL_ACCESS_ROLES = frozenset({"owner", "admin"})


def is_valid_permission(value: str) -> bool:
    return value in PERMISSION_EXPANSION


async def has_kb_permission(
    session: AsyncSession, principal: Principal, kb_id: uuid.UUID, action: Action
) -> bool:
    """先判存在性（RLS 已过滤其他租户），再判权限。

    跨租户 → NotFoundError(404)；同租户但无权 → 返回 False，由调用方转 403。
    这两个语义不能混：403 会泄露"资源存在"。
    """
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None or kb.deleted_at is not None:
        raise NotFoundError()

    if principal.role in FULL_ACCESS_ROLES:
        return True

    rows = (await session.execute(select(KbAcl).where(KbAcl.kb_id == kb_id))).scalars().all()

    granted: set[Action] = set()
    for row in rows:
        matches = (row.subject_type == "user" and row.subject_id == str(principal.user_id)) or (
            row.subject_type == "role" and row.subject_id == principal.role
        )
        if matches:
            granted |= PERMISSION_EXPANSION.get(row.permission, set())

    return action in granted


async def require_kb_permission(
    session: AsyncSession, principal: Principal, kb_id: uuid.UUID, action: Action
) -> None:
    if not await has_kb_permission(session, principal, kb_id, action):
        raise PermissionDeniedError("无权访问该知识库")


async def filter_readable_kbs(session: AsyncSession, principal: Principal) -> list[uuid.UUID]:
    """返回当前用户可读的知识库 id 列表。

    检索链路必须以本函数的结果为准，禁止直接信任请求里的 kb_ids。
    """
    result = await session.execute(
        select(KnowledgeBase.id).where(KnowledgeBase.deleted_at.is_(None))
    )
    all_ids = list(result.scalars().all())
    if not all_ids:
        return []
    if principal.role in FULL_ACCESS_ROLES:
        return all_ids

    acl_rows = (
        await session.execute(
            select(KbAcl.kb_id, KbAcl.permission, KbAcl.subject_type, KbAcl.subject_id).where(
                KbAcl.kb_id.in_(all_ids)
            )
        )
    ).all()

    allowed: dict[uuid.UUID, set[Action]] = {}
    for kb_id, permission, subject_type, subject_id in acl_rows:
        matches = (subject_type == "user" and subject_id == str(principal.user_id)) or (
            subject_type == "role" and subject_id == principal.role
        )
        if matches:
            allowed.setdefault(kb_id, set()).update(PERMISSION_EXPANSION.get(permission, set()))

    return [kb_id for kb_id in all_ids if Action.READ in allowed.get(kb_id, set())]
