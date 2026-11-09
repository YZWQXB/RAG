from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.conversation import service
from app.modules.conversation.schemas import ConversationCreate, ConversationOut, MessageOut

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(
    principal: CurrentPrincipal, session: DbSession
) -> list[ConversationOut]:
    return await service.list_conversations(session, principal)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, principal: CurrentPrincipal, session: DbSession
) -> ConversationOut:
    return await service.create_conversation(session, principal, payload.kb_ids, payload.title)


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> list[MessageOut]:
    return await service.get_messages(session, principal, conversation_id)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
):
    await service.delete_conversation(session, principal, conversation_id)
