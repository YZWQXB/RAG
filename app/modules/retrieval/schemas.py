from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class DebugRetrieveIn(BaseModel):
    kb_ids: list[uuid.UUID] = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    rerank: bool = True
