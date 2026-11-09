from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id, set_user_id
from app.core.db import session_scope
from app.core.errors import UnauthorizedError
from app.core.security import Principal, decode_token
from app.modules.iam.models import Membership

bearer_scheme = HTTPBearer(auto_error=False)


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal:
    """令牌只证明"曾经是成员"，不证明"现在仍是成员"。

    因此每个请求复核一次成员关系，并以库里的角色为准：
    - 成员被移除后旧令牌立即失效（否则最长可继续访问一个 token 生命周期）
    - 角色被降级后不会继续按旧角色放行
    代价是每请求多一次索引查询，与越权风险相比可以接受。
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError()

    token_principal = decode_token(credentials.credentials)
    set_user_id(token_principal.user_id)
    set_tenant_id(token_principal.tenant_id)

    async with session_scope(tenant_id=token_principal.tenant_id) as session:
        membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == token_principal.user_id)
            )
        ).scalar_one_or_none()

    if membership is None:
        raise UnauthorizedError("账号已不在该空间，请重新登录")

    if membership.role != token_principal.role:
        return replace(token_principal, role=membership.role)
    return token_principal


async def get_session(
    principal: Annotated[Principal, Depends(get_principal)],
) -> AsyncIterator[AsyncSession]:
    async with session_scope(tenant_id=principal.tenant_id) as session:
        yield session


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
