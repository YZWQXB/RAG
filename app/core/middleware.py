"""纯 ASGI 中间件：注入 trace_id。

刻意不用 BaseHTTPMiddleware：后者在历史版本中会缓冲流式响应，破坏 SSE。
"""

from __future__ import annotations

import time
import uuid

from app.core.context import reset_context, set_trace_id
from app.core.logging import get_logger
from app.core.metrics import observe_request

log = get_logger("http")

_SKIP_PATHS = {"/healthz", "/readyz", "/openapi.json", "/docs", "/redoc"}


class TraceMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        trace_id = headers.get("x-request-id") or uuid.uuid4().hex
        set_trace_id(trace_id)
        started = time.perf_counter()
        status_code = {"value": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code["value"] = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", trace_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - started
            if path not in _SKIP_PATHS:
                log.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_code["value"],
                    duration_ms=round(duration * 1000, 1),
                )
                observe_request(
                    scope.get("method", ""), path, status_code["value"] or 500, duration
                )
            reset_context()
