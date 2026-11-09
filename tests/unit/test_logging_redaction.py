from __future__ import annotations

import json

import pytest

from app.core.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _json_logs():
    configure_logging(level="INFO", json_output=True)
    yield


def _emit_and_capture(capsys) -> dict:
    logger = get_logger("test")
    logger.info(
        "check",
        api_key="sk-abcdefgh12345678",
        email="someone@example.com",
        phone="13812345678",
        password="hunter2",
        note="普通内容",
    )
    captured = capsys.readouterr().out.strip().splitlines()
    return json.loads(captured[-1])


def test_redacts_secrets_emails_and_phones(capsys):
    record = _emit_and_capture(capsys)
    serialized = json.dumps(record, ensure_ascii=False)
    assert "sk-abcdefgh12345678" not in serialized
    assert "someone@example.com" not in serialized
    assert "13812345678" not in serialized
    assert "hunter2" not in serialized
    assert "普通内容" in serialized


def test_includes_trace_id_when_set(capsys):
    from app.core.context import set_trace_id

    set_trace_id("trace-123")
    record = _emit_and_capture(capsys)
    assert record.get("trace_id") == "trace-123"
    set_trace_id(None)


def test_structured_fields_present(capsys):
    record = _emit_and_capture(capsys)
    assert record["event"] == "check"
    assert record["level"] == "info"
    assert "timestamp" in record
