"""How much rope the agent gets on one work order: plan | manual | automatic.

``orgs.agent_autonomy`` already gated side-effecting tools, but it is org-wide and
nothing ever set it — every org sits on ``high_touch``. The choice people actually
want is per job: think this one through before touching anything; or get on with
it and stop asking me.

* ``plan``      — read, plan, delegate, ask. Every write, execution and external
                  action is denied outright, so a plan-mode order cannot change
                  anything no matter what the model decides to try.
* ``manual``    — today's behaviour: the org posture decides what needs approval.
* ``automatic`` — approvals are granted without asking. Deliberately not the
                  default, and never the org default, because it lets an agent
                  send mail and call external tools with nobody in the loop.

Default ``manual`` so every existing order keeps exactly the behaviour it has.

Revision ID: 049
Revises: 048
"""

import sqlalchemy as sa
from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "mode")
