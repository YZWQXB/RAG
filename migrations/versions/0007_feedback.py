"""message feedback（点赞/点踩 + bad case 快照）

Revision ID: 0007_feedback
Revises: 0006_audit
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_feedback"
down_revision = "0006_audit"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    tid = "nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = {tid}) WITH CHECK (tenant_id = {tid})"
    )


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "message_feedback",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("snapshot", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
    )
    op.create_index("idx_feedback_tenant", "message_feedback", ["tenant_id", "created_at"])
    _enable_rls("message_feedback")


def downgrade() -> None:
    _drop_rls("message_feedback")
    op.drop_index("idx_feedback_tenant", table_name="message_feedback")
    op.drop_table("message_feedback")
