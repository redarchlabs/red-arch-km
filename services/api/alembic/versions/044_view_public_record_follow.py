"""Let a share link follow the newest record instead of pinning one forever.

Adds ``views.public_record_follow``. A share captures ``public_record_id`` at
enable time, which is right for a page about one fixed thing (a status board, a
check-in pad) and wrong for a page about "whatever is happening now". A class
quiz is the second kind: every lesson creates a new session record, so a link
shared once is stale from the next lesson onward — and it fails by rendering a
dead row's empty fields, which looks like a broken page rather than an expired
link.

When true, the anonymous render resolves the entity's newest record per request,
which is the same rule the authenticated ``record_id=latest`` sentinel already
uses for a wall display. The resolution stays entirely server-side and scoped to
the view's own org and entity, so the caller still cannot choose a row — the
guarantee the pin exists to provide is unchanged.

Defaults false, so every existing share keeps its fixed pin byte for byte.

Revision ID: 044
Revises: 043
"""

import sqlalchemy as sa
from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "views",
        sa.Column(
            "public_record_follow",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("views", "public_record_follow")
