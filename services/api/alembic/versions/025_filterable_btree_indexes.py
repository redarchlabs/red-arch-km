"""Backfill (org_id, col) btree indexes on filterable custom-entity columns.

New entity tables get a btree index per filterable scalar field at creation time
(see ``SchemaManager._create_btree_index``), so server-side record filters and the
reporting/aggregation engine's GROUP BY / HAVING / ORDER BY stay index-backed
rather than sequentially scanning. This migration brings *already-created* entity
tables in line by creating the same indexes for existing filterable fields.

It runs on the privileged Alembic connection (BYPASSRLS), so it sees every
tenant's catalog. Idempotent via ``CREATE INDEX IF NOT EXISTS`` — safe to re-run.
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
    conn = op.get_bind()
    import uuid

    for physical_table, physical_column, field_id_hex in _filterable_columns(conn):
        qt = identifiers.quote(physical_table)  # validates the generated identifier
        qc = identifiers.quote(physical_column)
        ix = identifiers.quote(identifiers.btree_index_name(uuid.UUID(field_id_hex)))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ix} ON {qt} (org_id, {qc})"))


def downgrade() -> None:
    conn = op.get_bind()
    import uuid

    for _physical_table, _physical_column, field_id_hex in _filterable_columns(conn):
        ix = identifiers.quote(identifiers.btree_index_name(uuid.UUID(field_id_hex)))
        conn.execute(text(f"DROP INDEX IF EXISTS {ix}"))
