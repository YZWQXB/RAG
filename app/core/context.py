"""请求级上下文变量：trace_id 与 tenant_id。

单独成文件是为了避免 logging / tenant / middleware 之间循环导入。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)
tenant_id_ctx: ContextVar[uuid.UUID | None] = ContextVar("tenant_id", default=None)
user_id_ctx: ContextVar[uuid.UUID | None] = ContextVar("user_id", default=None)


def set_trace_id(value: str | None) -> None:
    trace_id_ctx.set(value)


def get_trace_id() -> str | None:
    return trace_id_ctx.get()


def set_tenant_id(value: uuid.UUID | None) -> None:
    tenant_id_ctx.set(value)


def get_tenant_id() -> uuid.UUID | None:
    return tenant_id_ctx.get()


def set_user_id(value: uuid.UUID | None) -> None:
    user_id_ctx.set(value)


def get_user_id() -> uuid.UUID | None:
    return user_id_ctx.get()


def reset_context() -> None:
    trace_id_ctx.set(None)
    tenant_id_ctx.set(None)
    user_id_ctx.set(None)
