from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.v1.deps import CurrentPrincipal
from app.core.config import settings
from app.core.db import session_scope
from app.core.errors import PermissionDeniedError, RateLimitedError
from app.core.ratelimit import hit
from app.modules.generation.schemas import ChatIn
from app.modules.generation.service import stream_answer
from app.modules.iam.permissions import filter_readable_kbs
from app.modules.quota.service import assert_quota

router = APIRouter(prefix="/chat", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 关掉 nginx 缓冲，否则 token 会被攒成一批才下发
    "X-Accel-Buffering": "no",
}


async def _precheck(principal: CurrentPrincipal, payload: ChatIn) -> None:
    """流式响应一旦开始就无法改状态码，因此把 403/429 提前到路由层抛。"""
    # 先限流后查库：被刷的时候不该连数据库连接池一起耗掉
    allowed, retry_after = await hit(
        f"chat:{principal.user_id}", settings.chat_rate_limit_per_minute
    )
    if not allowed:
        raise RateLimitedError(retry_after)

    async with session_scope(tenant_id=principal.tenant_id) as session:
        readable = set(await filter_readable_kbs(session, principal))
        if not any(kb_id in readable for kb_id in payload.kb_ids):
            raise PermissionDeniedError("没有可访问的知识库")
        await assert_quota(session, principal.tenant_id, "queries_per_month", 1)
        await assert_quota(session, principal.tenant_id, "llm_tokens_per_month", 1)


@router.post("/stream")
async def chat_stream(payload: ChatIn, principal: CurrentPrincipal) -> StreamingResponse:
    await _precheck(principal, payload)
    return StreamingResponse(
        stream_answer(principal, payload),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
