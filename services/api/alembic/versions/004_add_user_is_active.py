"""Add user_profiles.is_active for site-admin deactivation (Slice 7).

Deactivated users are rejected at authentication time (403) even with a valid
Clerk JWT. Existing rows backfill to active via the server default, so the
migration is safe on a live database.

Revision ID: 004
Revises: 003
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "is_active")
