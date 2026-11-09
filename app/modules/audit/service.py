"""审计写入与查询。

写入必须与业务操作同一个事务：业务回滚了，日志也应该一起消失，
否则会出现"日志里有这次操作、数据里没有"的假记录。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.core.security import Principal
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogOut
from app.modules.iam.models import User
from app.modules.iam.permissions import FULL_ACCESS_ROLES


async def record(
    session: AsyncSession,
    principal: Principal,
    action: str,
    *,
    target_type: str = "",
    target_id: uuid.UUID | str | None = None,
    detail: dict | None = None,
) -> None:
    email = (
        await session.execute(select(User.email).where(User.id == principal.user_id))
    ).scalar_one_or_none()

    session.add(
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            actor_email=email or "",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            detail=detail or {},
        )
    )
    await session.flush()


async def list_logs(
    session: AsyncSession,
    principal: Principal,
    *,
    action: str | None = None,
    limit: int = 50,
) -> list[AuditLogOut]:
    if principal.role not in FULL_ACCESS_ROLES:
        raise PermissionDeniedError("只有空间所有者或管理员可以查看审计日志")

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)

    rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditLogOut(
            id=row.id,
            actor_id=row.actor_id,
            actor_email=row.actor_email,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            detail=row.detail,
            created_at=row.created_at,
        )
        for row in rows
    ]
