"""Diary entries get a real wall-clock timestamp, so one request's entries stay ordered.

``created_at`` defaulted to ``now()``, which in PostgreSQL is the **transaction**
start time — constant for the whole transaction. Every diary entry written in one
request therefore shared a timestamp to the microsecond, and the diary's keyset
order ``(created_at, id)`` fell through to the id: a random UUID.

So a request that wrote more than one entry displayed them in arbitrary order. A
reply and the note explaining what happened to it could appear inverted, which
reads as the system answering before it was asked.

``clock_timestamp()`` advances within a transaction, giving each row a distinct
value and restoring the append order the diary is read in. Existing rows keep
their ties; only new entries are affected.

Revision ID: 048
Revises: 047
"""

from alembic import op

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE work_order_entries ALTER COLUMN created_at SET DEFAULT clock_timestamp()")


def downgrade() -> None:
    op.execute("ALTER TABLE work_order_entries ALTER COLUMN created_at SET DEFAULT now()")
