"""structlog 配置与脱敏。"""

from __future__ import annotations

import logging
import re
import sys

import structlog

from app.core.context import get_tenant_id, get_trace_id

_SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_SENSITIVE_KEYS = {"password", "authorization", "api_key", "secret", "token"}


def _redact(value: str) -> str:
    value = _SECRET_RE.sub("***", value)
    value = _EMAIL_RE.sub("***", value)
    value = _PHONE_RE.sub("***", value)
    return value


def redaction_processor(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
        elif isinstance(event_dict[key], str):
            event_dict[key] = _redact(event_dict[key])
    return event_dict


def context_processor(_logger, _method, event_dict: dict) -> dict:
    trace_id = get_trace_id()
    tenant_id = get_tenant_id()
    if trace_id:
        event_dict.setdefault("trace_id", trace_id)
    if tenant_id:
        event_dict.setdefault("tenant_id", str(tenant_id))
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    renderer = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            context_processor,
            redaction_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app"):
    return structlog.get_logger(name)
