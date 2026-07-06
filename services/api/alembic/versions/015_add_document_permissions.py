"""Add per-document permission columns.

Documents can carry their own viewer/contributor permission configs and the
resolved access masks, independent of their folder. Seeded from the folder at
creation and overridable via the document's Properties. Mirrors the four
permission columns already on ``folders``.

A NULL ``viewer_permissions_config`` means "no per-document override" — the
document's entitlement then falls back to its folder's masks. Existing rows are
left NULL (empty masks) so they keep inheriting from their folder, which
matches the pre-migration behaviour.

Revision ID: 015
Revises: 014
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("view_permission_masks", postgresql.ARRAY(sa.BigInteger), server_default="{}", nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("contributor_permission_masks", postgresql.ARRAY(sa.BigInteger), server_default="{}", nullable=False),
    )
    op.add_column("documents", sa.Column("viewer_permissions_config", postgresql.JSONB, nullable=True))
    op.add_column("documents", sa.Column("contributor_permissions_config", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "contributor_permissions_config")
    op.drop_column("documents", "viewer_permissions_config")
    op.drop_column("documents", "contributor_permission_masks")
    op.drop_column("documents", "view_permission_masks")
