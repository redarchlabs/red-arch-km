"""Per-view public share token — anonymous access to SPECIFIC views.

Some views are meant to be used by someone who will never have a KM2 login: a
crew station on a shared tablet, a status board in a corridor, a check-in pad at
a front desk. Requiring a sign-in there is either impossible or means handing a
staff account to a room full of strangers.

Access is a capability in the URL, exactly like a public form link: an unguessable
token whose SHA-256 hash is all that is stored, so a database read cannot recover
a working link. It lives ON the view rather than in a links table because the
grant is a property of the page — one switch per view, rotate by re-enabling,
revoke by turning it off — and that keeps the anonymous surface to a single
nullable column instead of a new table with its own RLS.

``public_record_id`` PINS the record the anonymous render resolves, so a token
cannot be used to walk other records of the same entity.

Revision ID: 042
Revises: 041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("views", sa.Column("public_token_hash", sa.String(64), nullable=True))
    op.add_column("views", sa.Column("public_record_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("views", sa.Column("public_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("views", sa.Column("public_enabled_at", sa.DateTime(timezone=True), nullable=True))
    # Unique so a token identifies exactly one view; partial so the many views
    # with sharing OFF don't collide on NULL.
    op.create_index(
        "uq_views_public_token_hash",
        "views",
        ["public_token_hash"],
        unique=True,
        postgresql_where=sa.text("public_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_views_public_token_hash", table_name="views")
    op.drop_column("views", "public_enabled_at")
    op.drop_column("views", "public_expires_at")
    op.drop_column("views", "public_record_id")
    op.drop_column("views", "public_token_hash")
