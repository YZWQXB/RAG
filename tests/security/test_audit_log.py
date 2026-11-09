"""审计日志。

出事之后"谁在什么时候删了什么、改了谁的权限"必须查得到，
日志型数据不能依赖业务表（业务表会被软删、会被改）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


async def _logs(tenant_id, action: str | None = None) -> list[dict]:
    from app.core.db import session_scope

    sql = "SELECT action, target_type, actor_email, detail FROM audit_logs"
    params: dict = {}
    if action:
        sql += " WHERE action = :action"
        params["action"] = action
    sql += " ORDER BY created_at DESC"

    async with session_scope(tenant_id=tenant_id) as session:
        rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(row) for row in rows]


async def test_kb_creation_is_audited(client_a, tenant_a):
    created = (await client_a.post("/api/v1/kbs", json={"name": "审计库"})).json()

    logs = await _logs(tenant_a[0], "kb.create")
    assert len(logs) == 1
    assert logs[0]["target_type"] == "knowledge_base"
    assert logs[0]["detail"]["kb_id"] == created["id"]
    assert logs[0]["actor_email"]


async def test_acl_change_is_audited(client_a, tenant_a):
    kb = (await client_a.post("/api/v1/kbs", json={"name": "授权审计"})).json()
    await client_a.put(
        f"/api/v1/kbs/{kb['id']}/acl",
        json={"subject_type": "role", "subject_id": "viewer", "permission": "read"},
    )

    logs = await _logs(tenant_a[0], "kb.acl.upsert")
    assert logs and logs[0]["detail"]["subject_id"] == "viewer"
    assert logs[0]["detail"]["permission"] == "read"


async def test_member_removal_is_audited(client_a, tenant_a):
    from tests.helpers import create_member_token

    user_id, _ = await create_member_token(tenant_a[0], "viewer", "audit-target@example.com")
    assert (await client_a.delete(f"/api/v1/members/{user_id}")).status_code == 204

    logs = await _logs(tenant_a[0], "member.remove")
    assert logs and logs[0]["target_type"] == "user"


async def test_document_deletion_is_audited(client_a, tenant_a):
    from pathlib import Path

    fixtures = Path(__file__).resolve().parent.parent / "fixtures"
    kb_id = (await client_a.post("/api/v1/kbs", json={"name": "删档审计"})).json()["id"]
    doc = (
        await client_a.post(
            f"/api/v1/kbs/{kb_id}/documents",
            files={"file": ("a.md", (fixtures / "sample.md").read_bytes(), "text/markdown")},
        )
    ).json()

    await client_a.delete(f"/api/v1/documents/{doc['document_id']}")

    logs = await _logs(tenant_a[0], "document.delete")
    assert logs and logs[0]["detail"]["title"] == "a.md"


async def test_audit_logs_readable_by_owner_only(client_a, tenant_a):
    await client_a.post("/api/v1/kbs", json={"name": "可见性"})

    assert (await client_a.get("/api/v1/audit-logs")).status_code == 200

    from tests.helpers import client_with_token, create_member_token

    _, viewer_token = await create_member_token(tenant_a[0], "viewer", "audit-viewer@example.com")
    async with client_with_token(viewer_token) as viewer:
        assert (await viewer.get("/api/v1/audit-logs")).status_code == 403


async def test_audit_logs_are_tenant_isolated(client_a, client_b):
    await client_a.post("/api/v1/kbs", json={"name": "隔离审计"})
    assert (await client_b.get("/api/v1/audit-logs")).json() == []
