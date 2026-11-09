"""统一错误类型。所有对外文案为中文，且不含技术细节。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务异常的基类。子类通过类属性给出默认值，实例可覆盖。"""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "服务异常"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details: dict[str, Any] = details or {}
        self.headers: dict[str, str] = headers or {}
        super().__init__(self.message)


class BadRequestError(AppError):
    code = "BAD_REQUEST"
    status_code = 400
    message = "请求参数不合法"


class UnauthorizedError(AppError):
    code = "UNAUTHORIZED"
    status_code = 401
    message = "登录已失效，请重新登录"


class PermissionDeniedError(AppError):
    code = "FORBIDDEN"
    status_code = 403
    message = "无权执行该操作"


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    message = "资源不存在"


class ConflictError(AppError):
    code = "CONFLICT"
    status_code = 409
    message = "资源冲突"


class QuotaExceededError(AppError):
    code = "QUOTA_EXCEEDED"
    status_code = 429
    message = "本月额度已用尽"


class TenantContextMissing(AppError):
    code = "TENANT_CONTEXT_MISSING"
    status_code = 500
    message = "租户上下文缺失"


class UpstreamError(AppError):
    code = "UPSTREAM_ERROR"
    status_code = 502
    message = "上游服务暂时不可用，请稍后重试"


class ParseError(AppError):
    code = "PARSE_FAILED"
    status_code = 422
    message = "文档无法解析"


class RateLimitedError(AppError):
    """请求过于频繁。带 Retry-After，让客户端知道什么时候可以重试。"""

    code = "RATE_LIMITED"
    status_code = 429
    message = "请求过于频繁，请稍后再试"

    def __init__(self, retry_after: int = 60, message: str | None = None) -> None:
        super().__init__(
            message,
            details={"retry_after": retry_after},
            headers={"Retry-After": str(max(1, retry_after))},
        )
