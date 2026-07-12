"""Per-org agent autonomy posture — the high-touch approval gate.

Adds ``orgs.agent_autonomy`` (``high_touch`` | ``balanced`` | ``hands_off``,
default ``high_touch``). Under ``high_touch`` the agent authority engine forces
ASK on every *side-effecting* tool (external egress: email, Slack, MCP actions),
so a single human approves all outbound actions without each agent having to
enumerate them in ``grants.approval_required``. Internal record/document writes
are not side-effecting and stay allowed.

Revision ID: 033
Revises: 032
"""

import sqlalchemy as sa
from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column(
            "agent_autonomy",
            sa.String(length=16),
            nullable=False,
            server_default="high_touch",
        ),
    )


def downgrade() -> None:
    op.drop_column("orgs", "agent_autonomy")
