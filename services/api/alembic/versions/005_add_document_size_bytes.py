"""Add documents.size_bytes for file-explorer size sort.

Stores the original's size in bytes (uploaded file size, or byte-length of
pasted text). Nullable so existing rows are unaffected; they display "—" until
re-uploaded.

Revision ID: 005
Revises: 004
Create Date: 2026-07-05
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("size_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "size_bytes")
