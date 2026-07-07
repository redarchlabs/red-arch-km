"""workflow_connections: org-scoped, Fernet-encrypted connector credentials.

Revision ID: 019
Revises: 018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

# Same hardened tenant predicate as migration 018.
_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for action in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        suffix = action.lower()
        clause = "WITH CHECK" if action in ("INSERT", "UPDATE") else "USING"
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")


def upgrade() -> None:
    op.create_table(
        "workflow_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), server_default="http", nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("auth_type", sa.String(20), server_default="none", nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("config", JSONB(), server_default="{}", nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_workflow_connection_name_per_org"),
    )
    op.create_index("ix_workflow_connections_org_id", "workflow_connections", ["org_id"])
    _apply_rls("workflow_connections")
    # app_user (the RLS-enforced runtime role) needs table + sequence privileges.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_connections TO app_user")


def downgrade() -> None:
    op.drop_index("ix_workflow_connections_org_id", table_name="workflow_connections")
    op.drop_table("workflow_connections")
