from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.knowledge import acl_service, service
from app.modules.knowledge.schemas import AclIn, AclOut, KbCreate, KbOut, KbUpdate

router = APIRouter(prefix="/kbs", tags=["knowledge"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_kb(payload: KbCreate, principal: CurrentPrincipal, session: DbSession) -> KbOut:
    return await service.create_kb(session, principal, payload)


@router.get("")
async def list_kbs(principal: CurrentPrincipal, session: DbSession) -> list[KbOut]:
    return await service.list_kbs(session, principal)


@router.get("/{kb_id}")
async def get_kb(kb_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession) -> KbOut:
    return await service.get_kb(session, principal, kb_id)


@router.patch("/{kb_id}")
async def update_kb(
    kb_id: uuid.UUID, payload: KbUpdate, principal: CurrentPrincipal, session: DbSession
) -> KbOut:
    return await service.update_kb(session, principal, kb_id, payload)


@router.delete(
    "/{kb_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None
)
async def delete_kb(kb_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession):
    await service.delete_kb(session, principal, kb_id)


@router.put("/{kb_id}/acl")
async def upsert_acl(
    kb_id: uuid.UUID, payload: AclIn, principal: CurrentPrincipal, session: DbSession
) -> AclOut:
    return await acl_service.upsert_acl(session, principal, kb_id, payload)


@router.get("/{kb_id}/acl")
async def list_acl(
    kb_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> list[AclOut]:
    return await acl_service.list_acl(session, principal, kb_id)


@router.delete(
    "/{kb_id}/acl/{acl_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_acl(
    kb_id: uuid.UUID, acl_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
):
    return await acl_service.delete_acl(session, principal, kb_id, acl_id)
