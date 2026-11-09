from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.audit import service
from app.modules.audit.schemas import AuditLogOut

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
async def list_audit_logs(
    principal: CurrentPrincipal,
    session: DbSession,
    action: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AuditLogOut]:
    return await service.list_logs(session, principal, action=action, limit=limit)
