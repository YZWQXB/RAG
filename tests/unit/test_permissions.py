from __future__ import annotations

import uuid

import pytest

from app.core.db import session_scope
from app.core.security import Principal
from app.modules.iam.models import Membership, User
from app.modules.iam.permissions import Action, has_kb_permission
from app.modules.knowledge.models import KbAcl, KnowledgeBase

pytestmark = pytest.mark.db


async def _setup(tenant_id: uuid.UUID, owner_id: uuid.UUID, role: str, acl: str | None):
    async with session_scope(tenant_id=tenant_id) as session:
        kb = KnowledgeBase(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="kb",
            chunk_config={},
            created_by=owner_id,
        )
        session.add(kb)

        member_id = uuid.uuid4()
        session.add(
            User(
                id=member_id,
                email=f"{role}-{member_id.hex[:6]}@example.com",
                password_hash="x",
                name=role,
            )
        )
        await session.flush()
        session.add(Membership(id=uuid.uuid4(), tenant_id=tenant_id, user_id=member_id, role=role))
        if acl is not None:
            session.add(
                KbAcl(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    kb_id=kb.id,
                    subject_type="user",
                    subject_id=str(member_id),
                    permission=acl,
                )
            )
        await session.flush()

        principal = Principal(user_id=member_id, tenant_id=tenant_id, role=role)
        return kb.id, principal


@pytest.mark.parametrize(
    ("role", "acl", "action", "expected"),
    [
        ("owner", None, "manage", True),
        ("admin", None, "write", True),
        ("editor", None, "read", False),
        ("editor", "read", "read", True),
        ("editor", "read", "write", False),
        ("viewer", "read", "write", False),
        ("viewer", "manage", "write", True),
        ("viewer", "read", "read", True),
    ],
)
async def test_kb_permission_matrix(tenant_a, role, acl, action, expected):
    tenant_id, owner_id, _ = tenant_a
    async with session_scope(tenant_id=tenant_id) as session:
        kb_id, principal = await _setup(tenant_id, owner_id, role, acl)
        assert await has_kb_permission(session, principal, kb_id, Action(action)) is expected


async def test_missing_kb_raises_not_found(tenant_a):
    tenant_id, _, _ = tenant_a
    principal = Principal(user_id=uuid.uuid4(), tenant_id=tenant_id, role="owner")
    from app.core.errors import NotFoundError

    async with session_scope(tenant_id=tenant_id) as session:
        with pytest.raises(NotFoundError):
            await has_kb_permission(session, principal, uuid.uuid4(), Action.READ)
