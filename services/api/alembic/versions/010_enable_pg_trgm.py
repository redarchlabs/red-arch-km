"""Enable pg_trgm for index-backed substring search on custom-entity records.

Custom-entity record tables can grow to millions of rows. The records grid
supports case-insensitive substring search (``ILIKE '%q%'``) across text
columns; a plain B-tree cannot serve an unanchored ``LIKE``, so ``SchemaManager``
creates a per-column **trigram GIN** index for each searchable field. Those
indexes require the ``pg_trgm`` extension, enabled here once for the database.

Revision ID: 010
Revises: 009
Create Date: 2026-07-06
"""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: safe to re-run; no-op if already provisioned by the operator.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # Dropping the extension would drop dependent trigram indexes with it; only
    # do so when unwinding this migration explicitly.
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
