"""Per-org agent notify workflow — the external notification fan-out channel.

When set, a bubbled escalation / pending approval also fires this workflow (with
kind/title/body inputs), letting an org route agent notifications to Slack/Teams/
SMS via the existing workflow actions. Nullable; email + in-app always apply.

Revision ID: 031
Revises: 030
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain UUID (no FK): a direct orgs->workflows FK would add a second FK path
    # between the tables and make ORM joins ambiguous. A stale id no-ops the channel.
    op.add_column(
        "orgs",
        sa.Column("agent_notify_workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orgs", "agent_notify_workflow_id")
