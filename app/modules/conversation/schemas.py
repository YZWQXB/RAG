from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    kb_ids: list[uuid.UUID]
    message_count: int = 0
    updated_at: datetime | None = None


class CitationOut(BaseModel):
    document_id: uuid.UUID
    chunk_id: uuid.UUID | None = None
    title: str
    heading_path: str | None = None
    page_from: int
    rank: int


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    status: str
    created_at: datetime | None = None
    citations: list[CitationOut] = []


class ConversationCreate(BaseModel):
    kb_ids: list[uuid.UUID]
    title: str | None = None


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    message_id: uuid.UUID
    rating: str
    created: bool
