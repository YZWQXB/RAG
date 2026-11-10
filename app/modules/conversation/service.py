from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.security import Principal
from app.modules.conversation.models import (
    Conversation,
    Message,
    MessageCitation,
    MessageFeedback,
)
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


async def list_conversations(
    session: AsyncSession,
    principal: Principal,
    q: str | None = None,
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> list[ConversationOut]:
    """列出当前用户的会话，支持标题搜索与按更新时间排序。

    LIKE 的通配符必须转义：用户搜「50%」时，% 若当通配符会匹配到全部会话。
    """
    counts = (
        select(Message.conversation_id, func.count(Message.id).label("cnt"))
        .group_by(Message.conversation_id)
        .subquery()
    )

    stmt = (
        select(Conversation, func.coalesce(counts.c.cnt, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(
            Conversation.deleted_at.is_(None),
            Conversation.user_id == principal.user_id,
        )
    )

    keyword = (q or "").strip()
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Conversation.title.ilike(f"%{escaped}%", escape="\\"))

    if order == "asc":
        stmt = stmt.order_by(Conversation.updated_at.asc(), Conversation.id.asc())
    else:
        stmt = stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc())

    # 分页必须有上限：会话会一直增长，全量返回会让首屏越来越慢
    stmt = stmt.limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    return [_to_out(conversation, count) for conversation, count in rows]


async def get_messages(
    session: AsyncSession,
    principal: Principal,
    conversation_id: uuid.UUID,
    limit: int = 500,
) -> list[MessageOut]:
    """返回最近的 limit 条消息（仍按时间正序）。

    取法是「倒序取 N 条再翻转」，这样限制的是最近的消息而不是最早的——
    长会话里用户关心的是结尾，不是半年前的寒暄。
    """
    await _owned_conversation(session, principal, conversation_id)

    recent = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    messages = list(reversed(recent))

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
                chunk_id=citation.chunk_id,
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


async def upsert_feedback(
    session: AsyncSession,
    principal: Principal,
    message_id: uuid.UUID,
    rating: str,
    comment: str | None,
) -> tuple[str, bool]:
    """记录/更新对某条回答的反馈，返回 (rating, 是否新建)。

    快照里带上「问题 + 回答 + 引用」——只存 rating 没有价值，
    因为分析 bad case 时最需要知道当时检索到了什么、模型据此说了什么。
    """
    message = await session.get(Message, message_id)
    if message is None:
        raise NotFoundError()

    await _owned_conversation(session, principal, message.conversation_id)

    question = (
        await session.execute(
            select(Message.content)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.role == "user",
                Message.created_at <= message.created_at,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    citations = (
        (
            await session.execute(
                select(MessageCitation)
                .where(MessageCitation.message_id == message_id)
                .order_by(MessageCitation.rank)
            )
        )
        .scalars()
        .all()
    )
    snapshot = {
        "question": question or "",
        "answer": message.content[:1500],
        "status": message.status,
        "citations": [
            {
                "rank": item.rank,
                "document_id": str(item.document_id),
                "chunk_id": str(item.chunk_id) if item.chunk_id else None,
                "title": item.title,
                "heading_path": item.heading_path,
            }
            for item in citations
        ],
        "latency_ms": message.latency_ms,
        "token_out": message.token_out,
    }

    existing = (
        await session.execute(
            select(MessageFeedback).where(
                MessageFeedback.message_id == message_id,
                MessageFeedback.user_id == principal.user_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            MessageFeedback(
                id=uuid.uuid4(),
                tenant_id=principal.tenant_id,
                message_id=message_id,
                user_id=principal.user_id,
                rating=rating,
                comment=comment,
                snapshot=snapshot,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        return rating, True

    existing.rating = rating
    existing.comment = comment
    existing.snapshot = snapshot
    await session.flush()
    return rating, False


async def export_markdown(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> tuple[str, str]:
    """把会话导出为 Markdown，返回 (文件名, 内容)。

    引用按 [n] 展开成「文档 · 章节 · 页码」，这样粘贴到文档里仍然可追溯。
    """
    conversation = await _owned_conversation(session, principal, conversation_id)
    messages = await get_messages(session, principal, conversation_id)

    lines = [f"# {conversation.title}", ""]
    lines.append(f"> 导出时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    for message in messages:
        who = "我" if message.role == "user" else "助手"
        lines.append(f"## {who}")
        lines.append("")
        lines.append(message.content)
        lines.append("")
        if message.citations:
            lines.append("**引用**")
            lines.append("")
            for citation in message.citations:
                location = citation.heading_path or "正文"
                lines.append(
                    f"- [{citation.rank}] {citation.title} · {location} · "
                    f"第 {citation.page_from} 页"
                )
            lines.append("")

    safe_title = "".join(ch for ch in conversation.title if ch not in '\\/:*?"<>|').strip()
    return f"{safe_title or '会话'}.md", "\n".join(lines)
