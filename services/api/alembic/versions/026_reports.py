"""Reports: saved aggregation queries + visualization (the reporting engine).

A static, RLS-scoped tenant table (same hardened policy template as the rest of
the schema). Each row is a named GROUP BY / metric query (``query``) over one
entity plus a chart/table spec (``viz``). Dashboards and the ``chart`` / ``metric``
view elements reference a report by id, and reports travel in the org
import/export bundle.

Revision ID: 026
Revises: 025
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "entity_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("query", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("viz", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "slug", name="uq_report_slug_per_org"),
    )
    _apply_rls("reports")


def downgrade() -> None:
    for suffix, _a, _c in _POLICIES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON reports")
    op.drop_table("reports")
