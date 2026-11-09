from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.security import Principal
from app.modules.audit import service as audit
from app.modules.iam.models import Membership
from app.modules.iam.permissions import Action, require_kb_permission
from app.modules.knowledge.models import KbAcl
from app.modules.knowledge.schemas import AclIn, AclOut

VALID_ROLES = frozenset({"owner", "admin", "editor", "viewer"})


async def _validate_subject(session: AsyncSession, subject_type: str, subject_id: str) -> None:
    if subject_type == "role":
        if subject_id not in VALID_ROLES:
            raise BadRequestError("角色不存在", code="INVALID_SUBJECT")
        return

    try:
        user_id = uuid.UUID(subject_id)
    except ValueError as exc:
        raise BadRequestError("用户 ID 格式不正确", code="INVALID_SUBJECT") from exc

    membership = (
        await session.execute(select(Membership).where(Membership.user_id == user_id))
    ).scalar_one_or_none()
    if membership is None:
        raise BadRequestError("该用户不属于当前空间", code="INVALID_SUBJECT")


async def upsert_acl(
    session: AsyncSession, principal: Principal, kb_id: uuid.UUID, payload: AclIn
) -> AclOut:
    await require_kb_permission(session, principal, kb_id, Action.MANAGE)
    await _validate_subject(session, payload.subject_type, payload.subject_id)

    existing = (
        await session.execute(
            select(KbAcl).where(
                KbAcl.kb_id == kb_id,
                KbAcl.subject_type == payload.subject_type,
                KbAcl.subject_id == payload.subject_id,
                KbAcl.permission == payload.permission,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = KbAcl(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            kb_id=kb_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            permission=payload.permission,
        )
        session.add(existing)
        await session.flush()
        await audit.record(
            session,
            principal,
            "kb.acl.upsert",
            target_type="knowledge_base",
            target_id=kb_id,
            detail={
                "kb_id": str(kb_id),
                "subject_type": payload.subject_type,
                "subject_id": payload.subject_id,
                "permission": payload.permission,
            },
        )

    return AclOut(
        id=existing.id,
        subject_type=existing.subject_type,
        subject_id=existing.subject_id,
        permission=existing.permission,
    )


async def list_acl(session: AsyncSession, principal: Principal, kb_id: uuid.UUID) -> list[AclOut]:
    await require_kb_permission(session, principal, kb_id, Action.MANAGE)
    rows = (await session.execute(select(KbAcl).where(KbAcl.kb_id == kb_id))).scalars().all()
    return [
        AclOut(
            id=row.id,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            permission=row.permission,
        )
        for row in rows
    ]


async def delete_acl(
    session: AsyncSession, principal: Principal, kb_id: uuid.UUID, acl_id: uuid.UUID
) -> None:
    await require_kb_permission(session, principal, kb_id, Action.MANAGE)
    result = await session.execute(delete(KbAcl).where(KbAcl.id == acl_id, KbAcl.kb_id == kb_id))
    if result.rowcount == 0:
        raise NotFoundError()
    await audit.record(
        session,
        principal,
        "kb.acl.delete",
        target_type="knowledge_base",
        target_id=kb_id,
        detail={"kb_id": str(kb_id), "acl_id": str(acl_id)},
    )
