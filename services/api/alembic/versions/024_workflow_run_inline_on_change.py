"""workflows: add run_inline_on_change flag.

When true, an entity-change-triggered workflow runs INLINE in the request that
mutated the record (right after the write) instead of waiting for the celery beat
sweep of the workflow_outbox — killing the ~seconds trigger latency. Mirrors how
inbound webhooks already run inline. The record's outbox row is still written, so
the later sweep dedups the inline run (same workflow x outbox event) and still
fires any non-inline workflows on the same change.

Revision ID: 024
Revises: 023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "run_inline_on_change",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "run_inline_on_change")
