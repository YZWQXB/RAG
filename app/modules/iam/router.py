from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.iam import member_service
from app.modules.knowledge.schemas import MemberIn, MemberOut, MemberPatch

router = APIRouter(prefix="/members", tags=["members"])


@router.get("")
async def list_members(principal: CurrentPrincipal, session: DbSession) -> list[MemberOut]:
    return await member_service.list_members(session, principal)


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberIn, principal: CurrentPrincipal, session: DbSession
) -> MemberOut:
    return await member_service.add_member(session, principal, payload.email, payload.role)


@router.patch("/{user_id}")
async def update_member(
    user_id: uuid.UUID, payload: MemberPatch, principal: CurrentPrincipal, session: DbSession
) -> MemberOut:
    return await member_service.update_member_role(session, principal, user_id, payload.role)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def remove_member(user_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession):
    await member_service.remove_member(session, principal, user_id)
