"""Custom-entities catalog tables + RLS.

Creates the three catalog tables that describe user-defined entity types
(``entity_definitions``, ``entity_fields``, ``entity_relationships``) and puts
them under the same tenant-isolation RLS as every other tenant-scoped table.

The *physical* per-entity tables (``ce_<hex>`` / ``cej_<hex>``) are NOT created
here — they are created at request time by ``SchemaManager``, which applies the
identical RLS template to each one. This migration only creates the catalog.

Revision ID: 008
Revises: 007
Create Date: 2026-07-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

# Catalog tables that require RLS with org_id as tenant column. Mirrors the
# _RLS_TABLES pattern in migrations 001/002 and tests/integration/conftest.py.
_RLS_TABLES = [
    "entity_definitions",
    "entity_fields",
    "entity_relationships",
]

# Hardened tenant-isolation expression, identical to migration 002 (RED-3).
_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"

# (policy suffix, action keyword, using/with-check clause)
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def upgrade() -> None:
    op.create_table(
        "entity_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("physical_table", sa.String(63), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
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
        sa.UniqueConstraint("org_id", "slug", name="uq_entity_def_slug_per_org"),
        sa.UniqueConstraint("physical_table", name="uq_entity_def_physical_table"),
    )

    op.create_table(
        "entity_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("physical_column", sa.String(63), nullable=False),
        sa.Column("field_type", sa.String(30), nullable=False),
        sa.Column("picklist_options", postgresql.JSONB, server_default="[]"),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_unique", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("default_value", postgresql.JSONB, nullable=True),
        sa.Column("order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "entity_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("entity_definition_id", "slug", name="uq_entity_field_slug_per_def"),
    )

    op.create_table(
        "entity_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("cardinality", sa.String(20), nullable=False),
        sa.Column("on_delete", sa.String(10), nullable=False, server_default="SET NULL"),
        sa.Column("physical_name", sa.String(63), nullable=False),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_definitions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_definition_id", "slug", name="uq_entity_rel_slug_per_source"),
    )

    # --- Row-Level Security (same template as migrations 001/002) ---
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        for suffix, action, clause in _POLICIES:
            op.execute(
                f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})"
            )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def downgrade() -> None:
    for table in _RLS_TABLES:
        for suffix, _action, _clause in _POLICIES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop dependents first (entity_relationships/entity_fields FK entity_definitions).
    op.drop_table("entity_relationships")
    op.drop_table("entity_fields")
    op.drop_table("entity_definitions")
