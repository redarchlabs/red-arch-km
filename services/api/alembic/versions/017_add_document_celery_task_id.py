"""Add documents.celery_task_id for ingest cancel + per-job log correlation.

Persist the Celery task id of a document's ingest job so an in-flight ingest
can be cancelled (revoked) and its logs correlated. Nullable — rows whose
dispatch failed (broker outage) or that predate this column stay NULL, which
simply means "not cancellable / no job logs", matching pre-migration behaviour.

Revision ID: 017
Revises: 016
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "celery_task_id")
