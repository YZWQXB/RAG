from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Response, status

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.conversation import service
from app.modules.conversation.schemas import (
    ConversationCreate,
    ConversationOut,
    FeedbackIn,
    FeedbackOut,
    MessageOut,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(
    principal: CurrentPrincipal,
    session: DbSession,
    q: str | None = None,
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationOut]:
    return await service.list_conversations(session, principal, q, order, limit, offset)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, principal: CurrentPrincipal, session: DbSession
) -> ConversationOut:
    return await service.create_conversation(session, principal, payload.kb_ids, payload.title)


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[MessageOut]:
    return await service.get_messages(session, principal, conversation_id, limit)


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


@router.post("/messages/{message_id}/feedback")
async def submit_feedback(
    message_id: uuid.UUID,
    payload: FeedbackIn,
    principal: CurrentPrincipal,
    session: DbSession,
) -> FeedbackOut:
    rating, created = await service.upsert_feedback(
        session, principal, message_id, payload.rating, payload.comment
    )
    return FeedbackOut(message_id=message_id, rating=rating, created=created)


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> Response:
    filename, content = await service.export_markdown(session, principal, conversation_id)
    quoted = quote(filename)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
        },
    )
