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


class ChunkSourceOut(BaseModel):
    """引用定位到原文用：只回展示所需字段，不回向量。"""

    id: uuid.UUID
    document_id: uuid.UUID
    kb_id: uuid.UUID
    title: str
    chunk_index: int
    heading_path: str | None = None
    page_from: int = 1
    page_to: int = 1
    content: str
