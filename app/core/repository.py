"""仓储基类：自动注入租户过滤，并与 RLS 形成双保险。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base
from app.core.errors import NotFoundError
from app.core.tenant import current_tenant_id


class TenantScopedRepository:
    """子类设置 model 即可。所有查询都应经过 scope()。"""

    model: type[Base]

    def scope(self, stmt: Select) -> Select:
        return stmt.where(self.model.tenant_id == current_tenant_id())  # type: ignore[attr-defined]

    async def get_or_404(self, session: AsyncSession, ident: uuid.UUID) -> Any:
        obj = await session.get(self.model, ident)
        if obj is None:
            # RLS 已过滤掉其他租户的行，因此"查不到"同时覆盖了跨租户与不存在
            raise NotFoundError()
        return obj

    async def add(self, session: AsyncSession, obj: Any) -> Any:
        session.add(obj)
        await session.flush()
        return obj
