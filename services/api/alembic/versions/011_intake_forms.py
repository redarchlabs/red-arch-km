"""Intake forms: ``forms`` (definitions) and ``form_links`` (single-use tokens).

Both are static, RLS-scoped tenant tables (same hardened policy template as the
rest of the schema). ``form_links.token_hash`` is unique so a public request can
resolve the link — and thus the org — from the token before any tenant context
exists; the resolve happens on the privileged (BYPASSRLS) connection.

Revision ID: 011
Revises: 010
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]
_RLS_TABLES = ["forms", "form_links"]


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def upgrade() -> None:
    op.create_table(
        "forms",
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
        sa.UniqueConstraint("org_id", "slug", name="uq_form_slug_per_org"),
    )

    op.create_table(
        "form_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "form_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("forms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Token lookups happen before any tenant context is set (public path resolves
    # the org from the token), so the token_hash index is global and unique.
    op.execute("CREATE UNIQUE INDEX ix_form_links_token_hash ON form_links (token_hash)")

    op.execute(
        "ALTER TABLE form_links ADD CONSTRAINT ck_form_link_status "
        "CHECK (status IN ('pending','submitted','expired','revoked'))"
    )

    for table in _RLS_TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in _RLS_TABLES:
        for suffix, _a, _c in _POLICIES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {table}")
    op.drop_table("form_links")
    op.drop_table("forms")
