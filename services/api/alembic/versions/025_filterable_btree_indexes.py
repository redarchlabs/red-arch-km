"""Backfill (org_id, col DESC, id DESC) btree indexes on filterable columns.

New entity tables get a btree index per filterable scalar field at creation time
(see ``SchemaManager._create_btree_index``), so server-side record filters and the
reporting/aggregation engine's GROUP BY / HAVING / ORDER BY stay index-backed
rather than sequentially scanning. This migration brings *already-created* entity
tables in line by creating the same indexes for existing filterable fields.

Indexes are built ``CONCURRENTLY`` inside an ``autocommit_block`` so the backfill
never holds a table-wide write lock: a plain ``CREATE INDEX`` blocks all writers on
each ``ce_*`` table for the whole (multi-table) migration under Alembic's single
transaction, which would be a write outage on a live multi-tenant deployment. It
runs on the privileged connection (BYPASSRLS), so it sees every tenant's catalog.
Idempotent via ``IF NOT EXISTS`` — safe to re-run after a mid-build crash.

Note: ``downgrade()`` drops every index matching the deterministic name for a
currently-filterable field, so a rollback AFTER go-live also removes indexes that
ordinary field-creation added post-migration. Treat this as a backfill that is not
cleanly reversible once new fields exist (standard for data/index backfills).

Index names are derived deterministically from the field id (``btree_index_name``),
matching what the runtime DDL uses so the two never collide.

Revision ID: 025
Revises: 024
"""

from __future__ import annotations

from alembic import op
from api.services import identifiers
from api.services.schema_manager import FILTERABLE_FIELD_TYPES
from sqlalchemy import text

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def _filterable_columns(conn) -> list[tuple[str, str, str]]:
    """Return (physical_table, physical_column, field_id_hex) for every existing
    filterable field, read from the catalog."""
    rows = conn.execute(
        text(
            """
            SELECT d.physical_table, f.physical_column, f.id::text
            FROM entity_fields f
            JOIN entity_definitions d ON d.id = f.entity_definition_id
            WHERE f.field_type = ANY(:types)
            """
        ),
        {"types": list(FILTERABLE_FIELD_TYPES)},
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def upgrade() -> None:
    import uuid

    # Read the catalog in the normal transaction, then create indexes CONCURRENTLY
    # in autocommit (CREATE INDEX CONCURRENTLY cannot run inside a transaction).
    columns = _filterable_columns(op.get_bind())
    with op.get_context().autocommit_block():
        for physical_table, physical_column, field_id_hex in columns:
            qt = identifiers.quote(physical_table)  # validates the generated identifier
            qc = identifiers.quote(physical_column)
            ix = identifiers.quote(identifiers.btree_index_name(uuid.UUID(field_id_hex)))
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {ix} ON {qt} (org_id, {qc} DESC, id DESC)")


def downgrade() -> None:
    import uuid

    columns = _filterable_columns(op.get_bind())
    with op.get_context().autocommit_block():
        for _physical_table, _physical_column, field_id_hex in columns:
            ix = identifiers.quote(identifiers.btree_index_name(uuid.UUID(field_id_hex)))
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {ix}")
