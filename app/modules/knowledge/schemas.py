from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class KbUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class KbOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    document_count: int = 0
    updated_at: datetime | None = None


class AclIn(BaseModel):
    subject_type: str = Field(pattern="^(user|role)$")
    subject_id: str = Field(min_length=1, max_length=64)
    permission: str = Field(pattern="^(read|write|manage)$")


class AclOut(BaseModel):
    id: uuid.UUID
    subject_type: str
    subject_id: str
    permission: str


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: str


class MemberIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    role: str = Field(pattern="^(admin|editor|viewer)$")


class MemberPatch(BaseModel):
    role: str = Field(pattern="^(admin|editor|viewer)$")
