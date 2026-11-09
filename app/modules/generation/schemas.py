from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    kb_ids: list[uuid.UUID] = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None
