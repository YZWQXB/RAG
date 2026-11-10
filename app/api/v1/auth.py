"""认证接口。

注意：登录与换取 token 是**唯一需要跨租户读取**的业务路径（同一用户可能属于多个租户，
且此时还不知道要绑定哪个租户），因此这里显式使用 admin_session。这两个接口只读身份
相关表，且都先校验密码，返回的成员关系还会再按 user_id 过滤。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.v1.deps import CurrentPrincipal
from app.core.config import settings
from app.core.db import admin_session, session_scope
from app.core.errors import RateLimitedError
from app.core.ratelimit import client_ip, hit
from app.core.security import Principal, create_access_token
from app.modules.audit import service as audit
from app.modules.iam import service as iam_service
from app.modules.iam.models import User
from app.modules.iam.schemas import (
    LoginIn,
    LoginOut,
    RegisterIn,
    TenantBrief,
    TokenIn,
    TokenOut,
    UserOut,
)
from app.modules.tenancy.service import register_tenant

router = APIRouter(prefix="/auth", tags=["auth"])


async def _enforce_register_limit(request: Request) -> None:
    """按来源 IP 限制注册频率，避免批量刷号占库。"""
    allowed, retry_after = await hit(
        f"register:{client_ip(request)}", settings.register_rate_limit_per_minute
    )
    if not allowed:
        raise RateLimitedError(retry_after)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, request: Request) -> dict:
    await _enforce_register_limit(request)
    result = await register_tenant(
        email=body.email,
        password=body.password,
        tenant_name=body.tenant_name,
        slug=body.slug,
    )
    return result.model_dump(mode="json")


async def _enforce_login_limit(request: Request) -> None:
    """按来源 IP 限制登录尝试频率。

    账号锁定只能保护单个账号；攻击者换邮箱就能绕过，
    所以还需要一道与账号无关的、按来源计数的闸门。
    """
    allowed, retry_after = await hit(
        f"login:{client_ip(request)}", settings.login_rate_limit_per_minute
    )
    if not allowed:
        raise RateLimitedError(retry_after)


async def _audit_login(user_id, tenant_id, role: str, request: Request) -> None:
    """登录成功留痕，便于排查异常登录。失败不阻断登录。"""
    try:
        async with session_scope(tenant_id=tenant_id) as session:
            await audit.record(
                session,
                Principal(user_id=user_id, tenant_id=tenant_id, role=role),
                "auth.login",
                target_type="user",
                target_id=user_id,
                detail={"ip": client_ip(request)},
            )
    except Exception:  # 审计失败不能把用户挡在门外
        pass


@router.post("/login")
async def login(body: LoginIn, request: Request) -> dict:
    await _enforce_login_limit(request)
    async with admin_session() as session:
        user = await iam_service.authenticate(session, body.email, body.password)
        memberships = await iam_service.list_memberships(session, user.id)
        briefs = await iam_service.tenant_briefs(session, memberships)

    if not briefs:
        from app.core.errors import PermissionDeniedError

        raise PermissionDeniedError("该账号还未加入任何空间")

    if len(briefs) == 1:
        brief = briefs[0]
        await _audit_login(user.id, brief.id, brief.role, request)
        token = create_access_token(user.id, brief.id, brief.role)
        return LoginOut(
            token=token,
            tenant=brief,
            user=UserOut(id=user.id, email=user.email, name=user.name),
        ).model_dump(mode="json")

    return LoginOut(
        selection_required=True,
        tenants=briefs,
        user=UserOut(id=user.id, email=user.email, name=user.name),
    ).model_dump(mode="json")


@router.post("/token")
async def issue_token(body: TokenIn, request: Request) -> dict:
    await _enforce_login_limit(request)
    async with admin_session() as session:
        user = await iam_service.authenticate(session, body.email, body.password)
        membership = await iam_service.require_membership(session, user.id, body.tenant_id)
        briefs = await iam_service.tenant_briefs(session, [membership])

    if not briefs:
        from app.core.errors import NotFoundError

        raise NotFoundError()

    brief: TenantBrief = briefs[0]
    await _audit_login(user.id, brief.id, brief.role, request)
    token = create_access_token(user.id, brief.id, brief.role)
    return TokenOut(
        token=token, tenant=brief, user=UserOut(id=user.id, email=user.email, name=user.name)
    ).model_dump(mode="json")


@router.get("/me")
async def me(principal: CurrentPrincipal) -> dict:
    async with session_scope(tenant_id=principal.tenant_id) as session:
        user = await session.get(User, principal.user_id)
        if user is None:
            from app.core.errors import NotFoundError

            raise NotFoundError()
        return {
            "user": UserOut(id=user.id, email=user.email, name=user.name).model_dump(mode="json"),
            "tenant_id": str(principal.tenant_id),
            "role": principal.role,
        }
