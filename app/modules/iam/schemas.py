from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    tenant_name: str = Field(min_length=1, max_length=128)
    slug: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    email: str
    password: str


class TokenIn(BaseModel):
    email: str
    password: str
    tenant_id: uuid.UUID


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str


class TenantBrief(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    role: str


class TokenOut(BaseModel):
    token: str
    token_type: str = "bearer"
    tenant: TenantBrief
    user: UserOut


class LoginOut(BaseModel):
    token: str | None = None
    token_type: str = "bearer"
    tenant: TenantBrief | None = None
    user: UserOut | None = None
    selection_required: bool = False
    tenants: list[TenantBrief] = []
