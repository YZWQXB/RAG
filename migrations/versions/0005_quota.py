"""usage records

Revision ID: 0005_quota
Revises: 0004_conversation
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_quota"
down_revision = "0004_conversation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_usage_tenant_metric", "usage_records", ["tenant_id", "metric", "occurred_at"]
    )

    tid = "nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE usage_records FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON usage_records "
        f"USING (tenant_id = {tid}) WITH CHECK (tenant_id = {tid})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage_records")
    op.execute("ALTER TABLE usage_records NO FORCE ROW LEVEL SECURITY")
    op.drop_table("usage_records")
