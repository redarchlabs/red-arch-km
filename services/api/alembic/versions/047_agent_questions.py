"""Open questions an agent is blocked on — the answer-bearing half of the inbox.

``agent_approvals`` already gates "may I do this?" with yes/no. It cannot carry an
*answer*, so until now an agent had no way to obtain information it did not have:
``consult_peer`` filed a notification a human might read, and there was no way at
all to ask a person a question and use their reply.

``agent_questions`` closes that. A row is one blocked question — from an agent to a
human (``audience='human'``, raised by ``ask_human``) or to a peer agent
(``audience='agent'``, raised by ``consult_peer``, answered by ``reply_to_peer``).
``tool_call_id`` is the load-bearing column: it names the parked tool call in the
asking run's resume state, so the answer is injected as that call's result and the
run continues the same turn rather than restarting it.

``peer_run_id`` is indexed because every terminal transition of every run looks up
"was this run answering a consult?" to settle an unanswered one — without the index
that is a sequential scan on the hottest write path in the agents subsystem.

Revision ID: 047
Revises: 046
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None

# Same hardened RLS template every org-scoped table in this schema uses: the tenant
# GUC read is NULL-safe, so an unset GUC matches nothing rather than everything.
_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def upgrade() -> None:
    op.create_table(
        "agent_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tool_call_id", sa.String(120), nullable=False),
        sa.Column(
            "asked_by_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("audience", sa.String(10), nullable=False, server_default="human"),
        sa.Column(
            "target_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "peer_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "work_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("context", sa.Text, nullable=True),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending", index=True),
        sa.Column(
            "answered_by_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "answered_by_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # The inbox query ("what is waiting on a person right now?") and the settle
    # sweep both filter on status; a partial index keeps them off the answered
    # history, which is the part of the table that grows without bound.
    op.create_index(
        "ix_agent_questions_pending",
        "agent_questions",
        ["org_id", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.execute("ALTER TABLE agent_questions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_questions FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON agent_questions FOR {action} {clause} ({_HARDENED})")
    # Migration 034's blanket admin-bypass policy, applied here too so the worker's
    # cross-org sweep can settle questions on the privileged role.
    op.execute(
        "CREATE POLICY admin_bypass_all ON agent_questions FOR ALL "
        "USING (current_setting('app.bypass', true) = 'on') "
        "WITH CHECK (current_setting('app.bypass', true) = 'on')"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON agent_questions TO app_user")


def downgrade() -> None:
    op.drop_index("ix_agent_questions_pending", table_name="agent_questions")
    op.drop_table("agent_questions")
