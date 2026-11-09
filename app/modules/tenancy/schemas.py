from __future__ import annotations

import uuid

from pydantic import BaseModel


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    plan: str
    status: str


class RegisterOut(BaseModel):
    token: str
    token_type: str = "bearer"
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    user_id: uuid.UUID
