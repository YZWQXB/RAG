"""conversations, messages, citations

Revision ID: 0004_conversation
Revises: 0003_documents
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0004_conversation"
down_revision = "0003_documents"
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
        "conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kb_ids", ARRAY(sa.Uuid(as_uuid=True)), nullable=False),
        sa.Column("title", sa.String(128), nullable=False, server_default="新会话"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_conv_tenant", "conversations", ["tenant_id", "updated_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("token_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_msg_conv", "messages", ["tenant_id", "conversation_id", "created_at"])

    op.create_table(
        "message_citations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "message_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("chunk_id", sa.Uuid(as_uuid=True)),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("heading_path", sa.String(512)),
        sa.Column("page_from", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("idx_citation_msg", "message_citations", ["tenant_id", "message_id"])

    for table in ("conversations", "messages", "message_citations"):
        _enable_rls(table)


def downgrade() -> None:
    for table in ("message_citations", "messages", "conversations"):
        _drop_rls(table)
    op.drop_table("message_citations")
    op.drop_table("messages")
    op.drop_table("conversations")
