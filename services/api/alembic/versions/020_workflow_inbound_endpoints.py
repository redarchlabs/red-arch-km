"""workflow_inbound_endpoints: public webhook URLs that start workflow runs.

Revision ID: 020
Revises: 019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

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
        "workflow_inbound_endpoints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_wf_inbound_token_hash"),
    )
    op.create_index("ix_workflow_inbound_endpoints_token_hash", "workflow_inbound_endpoints", ["token_hash"])
    op.create_index("ix_workflow_inbound_endpoints_org_id", "workflow_inbound_endpoints", ["org_id"])
    op.create_index("ix_workflow_inbound_endpoints_workflow_id", "workflow_inbound_endpoints", ["workflow_id"])
    _apply_rls("workflow_inbound_endpoints")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_inbound_endpoints TO app_user")


def downgrade() -> None:
    op.drop_table("workflow_inbound_endpoints")
