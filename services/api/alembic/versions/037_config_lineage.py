"""Durable cross-environment lineage identity for config objects.

Change-management / release-promotion (see plan: multi-environment object
promotion) needs a stable identity for every configurable object that survives
being copied into another org/installation and survives a later rename. Today the
migration importer matches existing objects by *mutable* natural key (slug for
entities/forms/views/reports; ``name`` for workflows/connections/agents/mcp/tags;
path for folders), so renaming an object in one environment breaks its link to
the promoted copy in another.

This migration adds a nullable ``lineage_id`` UUID to each of the 14 config
tables. The semantics (implemented in the exporter/importer, not here):

* ``lineage_id IS NULL`` means **self-origin** — the object was authored in this
  environment and its lineage identity *is its own* ``id``. Every existing row is
  therefore already correctly identified with **zero backfill**.
* The exporter emits ``lineage_id := row.lineage_id or row.id``; the importer
  matches an incoming object to an existing one by ``(org_id, lineage_id)`` first
  (rename-proof) and stamps the incoming lineage onto a natural-key match on the
  first lineage-aware promotion.

The partial unique index guarantees a promotion re-targets **exactly one** row
per lineage within an org (and only indexes stamped rows, so it costs nothing for
the self-origin common case).

No RLS/grant changes are needed: these tables are already ``FORCE`` RLS-scoped by
``org_id`` and the new column lives on the already-scoped row.

Revision ID: 037
Revises: 036
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None

# The 14 config tables that participate in org portability / promotion, in the
# same set the migration bundle's RESOURCE_ORDER covers. Every one carries org_id.
_CONFIG_TABLES = (
    "entity_definitions",
    "entity_fields",
    "entity_relationships",
    "folders",
    "tags",
    "documents",
    "forms",
    "views",
    "reports",
    "workflows",
    "workflow_connections",
    "workflow_inbound_endpoints",
    "mcp_servers",
    "agents",
)


def _index_name(table: str) -> str:
    return f"uq_{table}_lineage"


def upgrade() -> None:
    for table in _CONFIG_TABLES:
        op.add_column(
            table,
            sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        # Partial unique index: a promotion resolves an incoming lineage to at
        # most one existing row per org. Only stamped (non-NULL) rows are indexed,
        # so self-origin rows (the common case) add nothing.
        op.create_index(
            _index_name(table),
            table,
            ["org_id", "lineage_id"],
            unique=True,
            postgresql_where=sa.text("lineage_id IS NOT NULL"),
        )


def downgrade() -> None:
    for table in reversed(_CONFIG_TABLES):
        op.drop_index(_index_name(table), table_name=table)
        op.drop_column(table, "lineage_id")
