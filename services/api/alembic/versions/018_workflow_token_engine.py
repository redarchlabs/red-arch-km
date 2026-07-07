"""Workflow token engine: per-run tokens + run/step columns for BPMN execution.

Adds the durable-token control-flow table and the run-scoped state the token
engine needs, all ADDITIVE so existing (legacy-walker) workflows keep running
unchanged:

- ``workflow_run_tokens``: RANGE-partitioned by ``created_at`` (composite PK
  ``(id, created_at)``, DEFAULT partition), FORCE RLS + policies mirroring
  migration 009, folded into ``workflow_ensure_partitions``.
- ``workflow_runs``: ``variables`` (run-scoped vars), ``step_seq`` (monotonic
  step counter), ``parent_run_id``/``parent_token_id`` (call-activity lineage),
  ``dead_letter``.
- ``workflow_run_steps``: ``token_id`` (branch attribution); ``step_index``
  widened smallint -> int.
- ``workflow_outbox`` status/source CHECK unchanged; ``OUTBOX_SOURCES`` gains
  ``webhook`` at the CHECK level here so a later inbound-webhook phase is a pure
  code change.

Revision ID: 018
Revises: 017
Create Date: 2026-07-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def upgrade() -> None:
    # --- workflow_runs: run-scoped token-engine state --------------------- #
    op.add_column(
        "workflow_runs",
        sa.Column("variables", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("step_seq", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("workflow_runs", sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("workflow_runs", sa.Column("parent_token_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "workflow_runs",
        sa.Column("dead_letter", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    # --- workflow_run_steps: token attribution + wider step_index --------- #
    op.add_column("workflow_run_steps", sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute("ALTER TABLE workflow_run_steps ALTER COLUMN step_index TYPE integer")

    # --- workflow_run_tokens (partitioned) -------------------------------- #
    op.create_table(
        "workflow_run_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=False), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("arrived_from_node_id", sa.String(64), nullable=True),
        sa.Column("arrived_via_handle", sa.String(64), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("wait_kind", sa.String(24), nullable=True),
        sa.Column("resume_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_key", sa.String(128), nullable=True),
        sa.Column("parent_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_node", sa.String(64), nullable=True),
        sa.Column("depth", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", "created_at"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.execute("CREATE TABLE workflow_run_tokens_default PARTITION OF workflow_run_tokens DEFAULT")
    op.execute("CREATE INDEX ix_wf_tokens_active ON workflow_run_tokens (seq) WHERE status = 'active'")
    op.execute(
        "CREATE INDEX ix_wf_tokens_timer ON workflow_run_tokens (resume_at) "
        "WHERE status = 'waiting' AND wait_kind IN ('timer','boundary','retry')"
    )
    op.execute(
        "CREATE INDEX ix_wf_tokens_correlation ON workflow_run_tokens (org_id, correlation_key) "
        "WHERE status = 'waiting' AND correlation_key IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_wf_tokens_stuck ON workflow_run_tokens (leased_at) WHERE status = 'running'")
    op.execute("CREATE INDEX ix_wf_tokens_run ON workflow_run_tokens (org_id, run_id)")
    op.execute(
        "ALTER TABLE workflow_run_tokens ADD CONSTRAINT ck_wf_token_status "
        "CHECK (status IN ('active','running','waiting','completed','dead'))"
    )

    # Outbox source vocabulary gains 'webhook' (inbound endpoints, later phase).
    op.execute("ALTER TABLE workflow_outbox DROP CONSTRAINT IF EXISTS ck_wf_outbox_source")
    op.execute(
        "ALTER TABLE workflow_outbox ADD CONSTRAINT ck_wf_outbox_source "
        "CHECK (source IN ('record','form','webhook'))"
    )

    # Fold the new partitioned table into the maintenance helper.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_ensure_partitions(months_ahead int DEFAULT 2) RETURNS void AS $$
        DECLARE
            tbl text;
            m int;
            start_date date;
            end_date date;
            part_name text;
        BEGIN
            FOREACH tbl IN ARRAY ARRAY['workflow_outbox','workflow_runs','workflow_run_steps','workflow_run_tokens'] LOOP
                FOR m IN 0..months_ahead LOOP
                    start_date := date_trunc('month', (now() AT TIME ZONE 'UTC' + (m || ' month')::interval))::date;
                    end_date := (start_date + interval '1 month')::date;
                    part_name := tbl || '_' || to_char(start_date, 'YYYYMM');
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                        part_name, tbl, start_date, end_date
                    );
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', part_name);
                END LOOP;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    _apply_rls("workflow_run_tokens")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_run_tokens_default TO app_user")


def downgrade() -> None:
    # Restore the maintenance helper to the pre-token set of tables.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_ensure_partitions(months_ahead int DEFAULT 2) RETURNS void AS $$
        DECLARE
            tbl text;
            m int;
            start_date date;
            end_date date;
            part_name text;
        BEGIN
            FOREACH tbl IN ARRAY ARRAY['workflow_outbox','workflow_runs','workflow_run_steps'] LOOP
                FOR m IN 0..months_ahead LOOP
                    start_date := date_trunc('month', (now() AT TIME ZONE 'UTC' + (m || ' month')::interval))::date;
                    end_date := (start_date + interval '1 month')::date;
                    part_name := tbl || '_' || to_char(start_date, 'YYYYMM');
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                        part_name, tbl, start_date, end_date
                    );
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', part_name);
                END LOOP;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for suffix, _a, _c in _POLICIES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON workflow_run_tokens")
    op.execute("DROP TABLE IF EXISTS workflow_run_tokens CASCADE")

    op.execute("ALTER TABLE workflow_outbox DROP CONSTRAINT IF EXISTS ck_wf_outbox_source")

    op.execute("ALTER TABLE workflow_run_steps ALTER COLUMN step_index TYPE smallint")
    op.drop_column("workflow_run_steps", "token_id")

    op.drop_column("workflow_runs", "dead_letter")
    op.drop_column("workflow_runs", "parent_token_id")
    op.drop_column("workflow_runs", "parent_run_id")
    op.drop_column("workflow_runs", "step_seq")
    op.drop_column("workflow_runs", "variables")
