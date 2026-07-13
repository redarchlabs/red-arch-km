"""Per-org home view — the optional landing/home screen.

When set, the sidebar shows a "Home" nav item linking to this view, letting an
org designate one view as its landing page. Nullable; unset means no Home item.

Revision ID: 036
Revises: 035
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain UUID (no FK): a direct orgs->views FK would add a second FK path
    # between the tables (views.org_id -> orgs.id already backs View.org) and make
    # that ORM join ambiguous. A stale id simply hides the Home item / 404s.
    op.add_column(
        "orgs",
        sa.Column("home_view_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orgs", "home_view_id")
