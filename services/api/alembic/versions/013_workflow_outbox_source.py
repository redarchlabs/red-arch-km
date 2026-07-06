"""Add ``source`` to workflow_outbox (distinguish form submissions).

A change captured by the outbox is either an ordinary edit (``record``) or a
public intake-form submission (``form``). A trigger can pin ``source="form"`` to
fire only on form submissions (the ``on_form_submission`` trigger).

Revision ID: 013
Revises: 012
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partitioned parent: adding a column propagates to all partitions.
    op.add_column(
        "workflow_outbox",
        sa.Column("source", sa.String(20), nullable=False, server_default="record"),
    )


def downgrade() -> None:
    op.drop_column("workflow_outbox", "source")
