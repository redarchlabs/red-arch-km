"""Add ``run_permission`` to workflows (who may manually run a workflow).

Configurable per workflow: org admins may always run; ``mode`` may additionally
open running to any member or to specific roles/groups. JSONB keeps the shape
flexible without a migration per policy tweak.

Revision ID: 012
Revises: 011
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "run_permission",
            postgresql.JSONB,
            nullable=False,
            server_default='{"mode": "org_admin"}',
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "run_permission")
