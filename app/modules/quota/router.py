from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.quota.service import usage_report

router = APIRouter(prefix="/quota", tags=["quota"])


@router.get("")
async def get_quota(principal: CurrentPrincipal, session: DbSession) -> dict:
    return await usage_report(session, principal.tenant_id)
