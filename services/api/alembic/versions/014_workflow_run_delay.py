"""Delay/wait support: parked runs resume from a saved point once due.

A run that reaches a ``delay`` node is parked as ``waiting`` with ``resume_at``
(when to continue) and ``resume_node_id`` (where to continue). The run-timers
job sweeps due waiting runs. Extends the run-status CHECK to allow ``waiting``.

Revision ID: 014
Revises: 013
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("resume_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workflow_runs", sa.Column("resume_node_id", sa.String(64), nullable=True))
    # Allow the new "waiting" status (CHECK lives on the partitioned parent).
    op.execute("ALTER TABLE workflow_runs DROP CONSTRAINT IF EXISTS ck_wf_run_status")
    op.execute(
        "ALTER TABLE workflow_runs ADD CONSTRAINT ck_wf_run_status "
        "CHECK (status IN ('pending','running','waiting','succeeded','failed','skipped'))"
    )
    # Partial index drives the run-timers sweep for due delayed runs.
    op.execute(
        "CREATE INDEX ix_wf_runs_waiting ON workflow_runs (resume_at) WHERE status = 'waiting'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wf_runs_waiting")
    op.execute("ALTER TABLE workflow_runs DROP CONSTRAINT IF EXISTS ck_wf_run_status")
    op.execute(
        "ALTER TABLE workflow_runs ADD CONSTRAINT ck_wf_run_status "
        "CHECK (status IN ('pending','running','succeeded','failed','skipped'))"
    )
    op.drop_column("workflow_runs", "resume_node_id")
    op.drop_column("workflow_runs", "resume_at")
