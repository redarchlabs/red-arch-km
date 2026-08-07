"""Per-org branding — a logo and an accent color for the pages an org presents.

Adds ``orgs.logo_object_key`` (the MinIO object key for an uploaded logo) and
``orgs.accent_color`` (a ``#rrggbb`` string). Both nullable: NULL is "unbranded",
which is today's behaviour byte for byte.

These surface on the chrome-free view routes — the kiosk screen a tablet runs and
the anonymous ``/s/<token>`` share page — where the app's own navigation is gone
and a page otherwise carries no identity at all. Storing the object KEY rather
than a URL keeps the asset behind the API, so a logo on a public page is served
through a token-scoped route rather than a guessable bucket path.

Revision ID: 046
Revises: 045
"""

import sqlalchemy as sa
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("logo_object_key", sa.String(length=500), nullable=True))
    op.add_column("orgs", sa.Column("accent_color", sa.String(length=7), nullable=True))
    # Showing the org's identity on an anonymous link is a disclosure decision,
    # so it is per-link and OFF unless the admin opts in when enabling sharing.
    op.add_column(
        "views",
        sa.Column("public_show_branding", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("views", "public_show_branding")
    op.drop_column("orgs", "accent_color")
    op.drop_column("orgs", "logo_object_key")
