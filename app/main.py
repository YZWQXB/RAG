from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core import metrics
from app.core.config import settings
from app.core.db import dispose_engines, session_scope
from app.core.errors import AppError
from app.core.http import close_http_client
from app.core.logging import configure_logging, get_logger
from app.core.middleware import TraceMiddleware

log = get_logger("app")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", [])[1:]),
                "reason": err.get("msg", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法",
                    "details": {"fields": fields},
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "服务异常", "details": {}}},
        )


def _start_embedding_warmup() -> asyncio.Task | None:
    """local 向量模式下后台预热模型。

    2.3GB 权重的首次加载要几十秒，如果落在第一个用户请求上，
    会表现为"问答超时"而不是"正在加载"。这里让它与启动并行发生。
    """
    if settings.embedding_provider != "local":
        return None

    async def _warm() -> None:
        try:
            from app.providers import get_embedding_provider

            await get_embedding_provider().embed(["预热"])
            log.info("embedding_warmup_done")
        except Exception as exc:  # 预热失败不应阻断服务启动
            log.warning("embedding_warmup_failed", error=type(exc).__name__)

    return asyncio.create_task(_warm())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(json_output=settings.is_production)
    log.info("startup", env=settings.env, embedding_provider=settings.embedding_provider)
    warmup = _start_embedding_warmup()
    yield
    if warmup is not None:
        warmup.cancel()
    await close_http_client()
    await dispose_engines()


def create_app() -> FastAPI:
    import app.core.registry  # noqa: F401  确保所有 ORM 模型已注册
    from app.api.v1 import api_router

    app = FastAPI(title="RAG SaaS", version="1.1.0", lifespan=lifespan)
    app.add_middleware(TraceMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        if not settings.metrics_enabled:
            return Response(status_code=404)
        await metrics.refresh_gauges()
        payload, content_type = metrics.render()
        return Response(content=payload, media_type=content_type)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        """存活探针：只要进程还能响应就是活着。

        刻意不查数据库与 Redis——把它们算进存活判定，会让一次缓存抖动
        升级成编排系统批量重启实例，故障被放大而不是被隔离。
        """
        return JSONResponse(status_code=200, content={"status": "alive"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """就绪探针：依赖都可用才算能对外服务，不通过则应从负载均衡摘除。"""
        checks: dict[str, str] = {}
        ok = True
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # 健康检查需要报告而非抛出
            checks["database"] = f"error: {type(exc).__name__}"
            ok = False

        from app.core.redis_client import ping_redis

        checks["redis"] = "ok" if await ping_redis() else "error"
        if checks["redis"] != "ok":
            ok = False

        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "degraded", "checks": checks},
        )

    return app


app = create_app()
