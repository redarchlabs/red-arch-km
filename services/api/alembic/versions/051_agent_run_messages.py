"""Steer — a durable mailbox for interjecting into a run that is already going.

The only channel to a running agent was answering a question it chose to ask. If
it set off in the wrong direction, the choice was to watch it finish or cancel it.

Delivery has to be a **pull**, not a push, because nothing outside the run can
tell whether it is safe to interrupt: ``status='running'`` covers streaming,
gating and mid-tool-batch identically, and injecting a user turn where a tool
result belongs is rejected outright by both OpenAI and Anthropic — which would
surface as an error that finalizes the run. So a steer is written here and the
loop drains it at the one seam where the message list is well-formed:

    UPDATE agent_run_messages SET delivered_at = now()
     WHERE run_id = :run AND delivered_at IS NULL RETURNING *

Atomic, exactly-once, and unaffected by Redis being down.

A table rather than ``agent_runs.input['steer']``: a JSONB append is a
read-modify-write, so two people steering at once would silently lose one of them.

Revision ID: 051
Revises: 050
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None

# The hardened RLS template every org-scoped table here uses: the tenant GUC read
# is NULL-safe, so an unset GUC matches nothing rather than everything.
_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def upgrade() -> None:
    op.create_table(
        "agent_run_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        # Who interjected, for the diary and for "who told it to do that?".
        sa.Column(
            "sent_by_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # NULL means still queued. Set by the drain, which is what makes delivery
        # exactly-once without a lock.
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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

    # The drain runs at the top of EVERY turn of EVERY run, and almost always finds
    # nothing. Partial index so that check stays off the delivered history, which
    # is the part of the table that grows.
    op.create_index(
        "ix_agent_run_messages_undelivered",
        "agent_run_messages",
        ["run_id", "created_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

    op.execute("ALTER TABLE agent_run_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_run_messages FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON agent_run_messages FOR {action} {clause} ({_HARDENED})")
    # Migration 034's blanket admin-bypass, so the worker's cross-org sweep can
    # drain on the privileged role.
    op.execute(
        "CREATE POLICY admin_bypass_all ON agent_run_messages FOR ALL "
        "USING (current_setting('app.bypass', true) = 'on') "
        "WITH CHECK (current_setting('app.bypass', true) = 'on')"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON agent_run_messages TO app_user")


def downgrade() -> None:
    op.drop_index("ix_agent_run_messages_undelivered", table_name="agent_run_messages")
    op.drop_table("agent_run_messages")
