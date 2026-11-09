from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: uuid.UUID
    kb_id: uuid.UUID
    title: str
    status: str
    stage: str | None = None
    error: str | None = None
    chunk_count: int = 0
    page_count: int = 0
    size_bytes: int = 0
    created_at: datetime | None = None


class UploadOut(BaseModel):
    document_id: uuid.UUID
    status: str
    stage: str | None = None
    duplicated: bool = False


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    size: int
