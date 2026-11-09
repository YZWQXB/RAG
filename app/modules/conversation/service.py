from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.security import Principal
from app.modules.conversation.models import Conversation, Message, MessageCitation
from app.modules.conversation.schemas import CitationOut, ConversationOut, MessageOut

DEFAULT_TITLE = "新会话"
TITLE_MAX_CHARS = 30


async def create_conversation(
    session: AsyncSession, principal: Principal, kb_ids: list[uuid.UUID], title: str | None
) -> ConversationOut:
    if not kb_ids:
        raise BadRequestError("请至少选择一个知识库", code="NO_KB")
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        kb_ids=list(kb_ids),
        title=(title or DEFAULT_TITLE)[:128],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(conversation)
    await session.flush()
    return _to_out(conversation, 0)


async def get_or_create(
    session: AsyncSession,
    principal: Principal,
    conversation_id: uuid.UUID | None,
    kb_ids: list[uuid.UUID],
) -> Conversation:
    if conversation_id is None:
        conversation = Conversation(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            kb_ids=list(kb_ids),
            title=DEFAULT_TITLE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(conversation)
        await session.flush()
        return conversation

    conversation = await _owned_conversation(session, principal, conversation_id)
    return conversation


async def _owned_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> Conversation:
    """会话是个人数据，必须同时满足同租户与同用户。

    RLS 只保证租户边界；跨用户读到他人会话属于 IDOR，所以这里再查一次 user_id。
    返回 404 而不是 403，避免暴露"该会话存在"。
    """
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.deleted_at is not None
        or conversation.user_id != principal.user_id
    ):
        raise NotFoundError()
    return conversation


async def list_conversations(session: AsyncSession, principal: Principal) -> list[ConversationOut]:
    counts = (
        select(Message.conversation_id, func.count(Message.id).label("cnt"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Conversation, func.coalesce(counts.c.cnt, 0))
            .outerjoin(counts, counts.c.conversation_id == Conversation.id)
            .where(
                Conversation.deleted_at.is_(None),
                Conversation.user_id == principal.user_id,
            )
            .order_by(Conversation.updated_at.desc())
        )
    ).all()
    return [_to_out(conversation, count) for conversation, count in rows]


async def get_messages(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> list[MessageOut]:
    await _owned_conversation(session, principal, conversation_id)

    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )

    citations = (
        (
            await session.execute(
                select(MessageCitation)
                .where(
                    MessageCitation.message_id.in_([m.id for m in messages]) if messages else False
                )
                .order_by(MessageCitation.rank)
            )
        )
        .scalars()
        .all()
        if messages
        else []
    )

    by_message: dict[uuid.UUID, list[CitationOut]] = {}
    for citation in citations:
        by_message.setdefault(citation.message_id, []).append(
            CitationOut(
                document_id=citation.document_id,
                title=citation.title,
                heading_path=citation.heading_path,
                page_from=citation.page_from,
                rank=citation.rank,
            )
        )

    return [
        MessageOut(
            id=message.id,
            role=message.role,
            content=message.content,
            status=message.status,
            created_at=message.created_at,
            citations=by_message.get(message.id, []),
        )
        for message in messages
    ]


async def delete_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> None:
    conversation = await _owned_conversation(session, principal, conversation_id)
    conversation.deleted_at = datetime.now(UTC)
    await session.flush()


async def add_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    status: str = "normal",
    token_in: int = 0,
    token_out: int = 0,
    latency_ms: int = 0,
    trace_id: str | None = None,
) -> Message:
    message = Message(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role=role,
        content=content,
        status=status,
        token_in=token_in,
        token_out=token_out,
        latency_ms=latency_ms,
        trace_id=trace_id,
        created_at=datetime.now(UTC),
    )
    session.add(message)
    await session.flush()
    return message


async def add_citations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    citations: list[dict],
) -> None:
    for rank, item in enumerate(citations, start=1):
        session.add(
            MessageCitation(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                message_id=message_id,
                document_id=item["document_id"],
                chunk_id=item.get("chunk_id"),
                title=item.get("title", ""),
                heading_path=item.get("heading_path"),
                page_from=item.get("page_from", 1),
                rank=rank,
            )
        )
    await session.flush()


async def touch_conversation(session: AsyncSession, conversation: Conversation) -> None:
    conversation.updated_at = datetime.now(UTC)
    await session.flush()


async def maybe_set_title(session: AsyncSession, conversation: Conversation, query: str) -> None:
    if conversation.title != DEFAULT_TITLE:
        return
    conversation.title = query.strip()[:TITLE_MAX_CHARS] or DEFAULT_TITLE
    conversation.updated_at = datetime.now(UTC)
    await session.flush()


def _to_out(conversation: Conversation, count: int) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        kb_ids=list(conversation.kb_ids),
        message_count=count,
        updated_at=conversation.updated_at,
    )
