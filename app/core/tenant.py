"""租户上下文与 RLS 绑定。

这里的 SET LOCAL 是整个多租户隔离的核心契约：
- 必须是 SET LOCAL（事务级），不能用 SET（会话级）。连接从池中归还后再被
  其他租户取用时，SET 的残留值会造成跨租户数据泄露。
- SET LOCAL 依赖活动事务，所以必须在 session.begin() 之后调用。
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_tenant_id, set_tenant_id
from app.core.errors import TenantContextMissing


async def bind_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    # 用 set_config(name, value, is_local=true) 而不是 SET LOCAL：
    # PostgreSQL 的 SET 语句不接受绑定参数（会报 syntax error at or near "$1"），
    # 而 set_config 的第三个参数 true 与 SET LOCAL 语义完全一致（事务结束即失效）。
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )
    set_tenant_id(tenant_id)


def current_tenant_id() -> uuid.UUID:
    tenant_id = get_tenant_id()
    if tenant_id is None:
        raise TenantContextMissing()
    return tenant_id


def reset_tenant() -> None:
    set_tenant_id(None)
