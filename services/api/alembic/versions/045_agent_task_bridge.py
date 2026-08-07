"""Bridge workflows to agents: a BPMN ``agent`` task parks its token on a queued
``AgentRun`` and the run's terminal state resumes the token.

Columns on ``agent_runs`` (NOT a bridge table: a new table would need the full
RLS block by hand — ENABLE/FORCE, tenant policies, ``admin_bypass_all``, grants —
and migrations 034/040 showed how silently that goes wrong; ``agent_runs`` is a
plain table already covered by 034):

- ``workflow_run_id`` / ``workflow_run_created_at`` / ``workflow_node_id`` /
  ``workflow_token_id`` — soft references (``workflow_runs``/``workflow_run_tokens``
  are RANGE-partitioned with composite PKs, so no FK is possible and retention
  partition-drops must not cascade). ``*_created_at`` keeps every lookup
  partition-local.
- ``output`` — the schema-validated ``complete_task`` object.
- ``agents.workflow_invocable`` — agent-side consent list mirroring
  ``workflow_allowlist``: which workflows may bind this agent to an agent_task
  node (confused-deputy guard).

Also swaps the ``ix_wf_tokens_timer`` partial-index predicate to include the new
``agent`` wait kind, so an armed SLA timer boundary on an agent task is found by
the timer sweep (which now scans ``wait_kind IN ('timer','boundary','retry','agent')``
for due tokens).

Revision ID: 045
Revises: 044
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("workflow_run_id", UUID(as_uuid=True), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_run_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_node_id", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_token_id", UUID(as_uuid=True), nullable=True))
    op.add_column("agent_runs", sa.Column("output", JSONB(), nullable=True))
    # Reverse lookup (run-monitor UI) + cancellation propagation scan (token-death
    # paths cancel linked non-terminal runs).
    op.create_index(
        "ix_agent_runs_workflow_run",
        "agent_runs",
        ["workflow_run_id"],
        postgresql_where=sa.text("workflow_run_id IS NOT NULL"),
    )

    op.add_column(
        "agents",
        sa.Column("workflow_invocable", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    # Timer sweep must see armed agent-task SLA parks. (Partitioned parent index:
    # recreate with the widened predicate; partitions inherit.)
    op.drop_index("ix_wf_tokens_timer", table_name="workflow_run_tokens")
    op.create_index(
        "ix_wf_tokens_timer",
        "workflow_run_tokens",
        ["resume_at"],
        postgresql_where=sa.text("status = 'waiting' AND wait_kind IN ('timer','boundary','retry','agent')"),
    )


def downgrade() -> None:
    op.drop_index("ix_wf_tokens_timer", table_name="workflow_run_tokens")
    op.create_index(
        "ix_wf_tokens_timer",
        "workflow_run_tokens",
        ["resume_at"],
        postgresql_where=sa.text("status = 'waiting' AND wait_kind IN ('timer','boundary','retry')"),
    )
    op.drop_column("agents", "workflow_invocable")
    op.drop_index("ix_agent_runs_workflow_run", table_name="agent_runs")
    op.drop_column("agent_runs", "output")
    op.drop_column("agent_runs", "workflow_token_id")
    op.drop_column("agent_runs", "workflow_node_id")
    op.drop_column("agent_runs", "workflow_run_created_at")
    op.drop_column("agent_runs", "workflow_run_id")
