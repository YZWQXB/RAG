from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None = None
    actor_email: str
    action: str
    target_type: str
    target_id: str | None = None
    detail: dict
    created_at: datetime | None = None
