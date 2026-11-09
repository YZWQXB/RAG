from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.core.errors import NotFoundError
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.schemas import TenantOut

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> TenantOut:
    # RLS 只放行当前租户的行，访问他人 id 必然查不到 → 404（不泄露存在性）
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError()
    return TenantOut(
        id=tenant.id, slug=tenant.slug, name=tenant.name, plan=tenant.plan, status=tenant.status
    )
