from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, PermissionDeniedError
from app.core.security import Principal
from app.modules.audit import service as audit
from app.modules.iam.permissions import Action, filter_readable_kbs, require_kb_permission
from app.modules.knowledge.models import DEFAULT_CHUNK_CONFIG, KbAcl, KnowledgeBase
from app.modules.knowledge.repository import KnowledgeBaseRepository
from app.modules.knowledge.schemas import KbCreate, KbOut, KbUpdate

repo = KnowledgeBaseRepository()

CREATOR_ROLES = frozenset({"owner", "admin", "editor"})


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise BadRequestError("知识库名称不能为空", code="INVALID_NAME")
    return cleaned


def _to_out(kb: KnowledgeBase, count: int) -> KbOut:
    return KbOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        status=kb.status,
        document_count=count,
        updated_at=kb.updated_at,
    )


async def create_kb(session: AsyncSession, principal: Principal, payload: KbCreate) -> KbOut:
    if principal.role not in CREATOR_ROLES:
        raise PermissionDeniedError("当前角色不能创建知识库")

    kb = KnowledgeBase(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        name=_clean_name(payload.name),
        description=payload.description,
        chunk_config=dict(DEFAULT_CHUNK_CONFIG),
        status="active",
        created_by=principal.user_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(kb)
    await session.flush()
    # 创建人未必是 owner/admin（editor 也能建库），而 editor 不在 FULL_ACCESS_ROLES 里，
    # 没有这条 ACL，创建人对自己刚建的库连 WRITE 都没有——建库即失权的悖论。
    # 显式授予 manage，保证创建人可以管理自己的库（含后续授权他人）。
    session.add(
        KbAcl(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            kb_id=kb.id,
            subject_type="user",
            subject_id=str(principal.user_id),
            permission="manage",
        )
    )
    await session.flush()
    await audit.record(
        session,
        principal,
        "kb.create",
        target_type="knowledge_base",
        target_id=kb.id,
        detail={"kb_id": str(kb.id), "name": kb.name},
    )
    return _to_out(kb, 0)


async def list_kbs(session: AsyncSession, principal: Principal) -> list[KbOut]:
    readable = await filter_readable_kbs(session, principal)
    if not readable:
        return []
    kbs = await repo.list_active(session, readable)
    counts = await repo.document_counts(session, [kb.id for kb in kbs])
    return [_to_out(kb, counts.get(kb.id, 0)) for kb in kbs]


async def get_kb(session: AsyncSession, principal: Principal, kb_id: uuid.UUID) -> KbOut:
    await require_kb_permission(session, principal, kb_id, Action.READ)
    kb = await session.get(KnowledgeBase, kb_id)
    counts = await repo.document_counts(session, [kb_id])
    return _to_out(kb, counts.get(kb_id, 0))


async def update_kb(
    session: AsyncSession, principal: Principal, kb_id: uuid.UUID, payload: KbUpdate
) -> KbOut:
    await require_kb_permission(session, principal, kb_id, Action.WRITE)
    kb = await session.get(KnowledgeBase, kb_id)
    if payload.name is not None:
        kb.name = _clean_name(payload.name)
    if payload.description is not None:
        kb.description = payload.description
    # 显式赋值而非依赖 onupdate=func.now()：后者会让属性在 flush 后过期，
    # 同步构造响应模型时访问该字段会触发 IO（MissingGreenlet）。
    kb.updated_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        principal,
        "kb.update",
        target_type="knowledge_base",
        target_id=kb_id,
        detail={"kb_id": str(kb_id), "name": kb.name},
    )
    counts = await repo.document_counts(session, [kb_id])
    return _to_out(kb, counts.get(kb_id, 0))


async def delete_kb(session: AsyncSession, principal: Principal, kb_id: uuid.UUID) -> None:
    await require_kb_permission(session, principal, kb_id, Action.MANAGE)
    kb = await session.get(KnowledgeBase, kb_id)
    kb.deleted_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        principal,
        "kb.delete",
        target_type="knowledge_base",
        target_id=kb_id,
        detail={"kb_id": str(kb_id), "name": kb.name},
    )
