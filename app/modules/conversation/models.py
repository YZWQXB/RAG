from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Uuid as UUIDType
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("idx_conv_tenant", "tenant_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    kb_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUIDType(as_uuid=True)), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新会话")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("idx_msg_conv", "tenant_id", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    token_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MessageCitation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (Index("idx_citation_msg", "tenant_id", "message_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType(as_uuid=True))
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    heading_path: Mapped[str | None] = mapped_column(String(512))
    page_from: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MessageFeedback(Base):
    """对回答的显式反馈。

    这是唯一能拿到「用户真实判定为错」的渠道：检索分数高不代表答得对，
    而 bad case（含当时的引用与分数快照）是后续调阈值、换模型、改提示词的依据。
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
        Index("idx_feedback_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
