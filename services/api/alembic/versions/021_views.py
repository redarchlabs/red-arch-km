"""Views: composable screens (``views``) rendered by the shared form renderer.

A static, RLS-scoped tenant table (same hardened policy template as the rest of
the schema). ``entity_definition_id`` is nullable — a view may bind to an entity
(to render/edit a record) or stand alone as a dashboard of embedded forms +
action buttons.

Revision ID: 021
Revises: 020
Create Date: 2026-07-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "021"
down_revision = "020"
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
        "views",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "entity_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_definitions.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
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
        sa.UniqueConstraint("org_id", "slug", name="uq_view_slug_per_org"),
    )
    _apply_rls("views")


def downgrade() -> None:
    for suffix, _a, _c in _POLICIES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON views")
    op.drop_table("views")
