"""Workflow engine: definitions, versions, and partitioned outbox/runs/steps.

- ``workflows`` / ``workflow_versions``: static, RLS-scoped. A trigger makes a
  published version immutable (only ``status`` may change, e.g. archiving).
- ``workflow_outbox`` / ``workflow_runs`` / ``workflow_run_steps``:
  RANGE-partitioned by ``created_at`` with a DEFAULT partition (rows always land
  somewhere). ``workflow_ensure_partitions(months_ahead)`` pre-creates month
  partitions; a celery-beat job calls it.

RLS policies are created on the partitioned *parents*; Postgres enforces them on
all partitions.

Revision ID: 009
Revises: 008
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]
_RLS_TABLES = [
    "workflows",
    "workflow_versions",
    "workflow_outbox",
    "workflow_runs",
    "workflow_run_steps",
]
_PARTITIONED = ["workflow_outbox", "workflow_runs", "workflow_run_steps"]


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "entity_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name", name="uq_workflow_name_per_org"),
    )

    op.create_table(
        "workflow_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("definition", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "published_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version_number"),
    )

    # --- Partitioned: outbox ---
    op.create_table(
        "workflow_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=False), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_table", sa.String(63), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("before_data", postgresql.JSONB, nullable=True),
        sa.Column("after_data", postgresql.JSONB, nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dedup_key", sa.String(128), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.SmallInteger, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", "created_at"),
        postgresql_partition_by="RANGE (created_at)",
    )
    # The dispatcher claims with ORDER BY seq; index seq alone (not
    # created_at, seq) so the claim uses a Merge Append index scan, not a sort.
    op.execute("CREATE INDEX ix_wf_outbox_pending ON workflow_outbox (seq) WHERE status = 'pending'")
    op.execute(
        "CREATE INDEX ix_wf_outbox_entity ON workflow_outbox (org_id, entity_definition_id) "
        "WHERE status = 'pending'"
    )

    # --- Partitioned: runs ---
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outbox_seq", sa.BigInteger, nullable=True),
        sa.Column("trigger_operation", sa.String(10), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("conditions_matched", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("input_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("depth", sa.SmallInteger, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", "created_at"),
        sa.UniqueConstraint("org_id", "outbox_id", "workflow_id", "created_at", name="uq_wf_run_event"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.execute("CREATE INDEX ix_wf_runs_workflow ON workflow_runs (org_id, workflow_id, created_at)")

    # --- Partitioned: run steps ---
    op.create_table(
        "workflow_run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("step_index", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.SmallInteger, nullable=False, server_default="3"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input", postgresql.JSONB, nullable=True),
        sa.Column("output", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", "created_at"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.execute("CREATE INDEX ix_wf_steps_run ON workflow_run_steps (org_id, run_id, step_index)")

    # Default partitions so inserts always succeed.
    for tbl in _PARTITIONED:
        op.execute(f"CREATE TABLE {tbl}_default PARTITION OF {tbl} DEFAULT")

    # CHECK constraints for the status/operation vocabularies (defended at the DB
    # so a direct SQL write can't introduce a value the dispatcher won't match).
    op.execute(
        "ALTER TABLE workflow_versions ADD CONSTRAINT ck_wf_version_status "
        "CHECK (status IN ('draft','published','archived'))"
    )
    op.execute(
        "ALTER TABLE workflow_outbox ADD CONSTRAINT ck_wf_outbox_status "
        "CHECK (status IN ('pending','claimed','done','skipped'))"
    )
    op.execute(
        "ALTER TABLE workflow_outbox ADD CONSTRAINT ck_wf_outbox_operation "
        "CHECK (operation IN ('create','update','delete'))"
    )
    op.execute(
        "ALTER TABLE workflow_runs ADD CONSTRAINT ck_wf_run_status "
        "CHECK (status IN ('pending','running','succeeded','failed','skipped'))"
    )
    op.execute(
        "ALTER TABLE workflow_run_steps ADD CONSTRAINT ck_wf_step_status "
        "CHECK (status IN ('pending','running','succeeded','failed','skipped','retrying'))"
    )

    # Published-version immutability trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_versions_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'published' AND (
                NEW.definition IS DISTINCT FROM OLD.definition
                OR NEW.version_number IS DISTINCT FROM OLD.version_number
                OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id
            ) THEN
                RAISE EXCEPTION 'published workflow versions are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_workflow_versions_immutable BEFORE UPDATE ON workflow_versions "
        "FOR EACH ROW EXECUTE FUNCTION workflow_versions_immutable()"
    )

    # Month-partition maintenance helper (idempotent).
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
                    -- Anchor month boundaries to UTC so ranges align with the
                    -- timestamptz values regardless of the session TimeZone GUC.
                    start_date := date_trunc('month', (now() AT TIME ZONE 'UTC' + (m || ' month')::interval))::date;
                    end_date := (start_date + interval '1 month')::date;
                    part_name := tbl || '_' || to_char(start_date, 'YYYYMM');
                    -- CREATE TABLE IF NOT EXISTS is race-free (concurrent callers
                    -- no-op with a NOTICE) and schema-qualified, unlike a
                    -- check-then-act on pg_class.relname.
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                        part_name, tbl, start_date, end_date
                    );
                    -- GRANT is not inherited by partitions; keep parity with the
                    -- default partition so any direct-partition access works.
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', part_name);
                END LOOP;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    for table in _RLS_TABLES:
        _apply_rls(table)
    # Grant on the default partitions too (RLS inherits from the parent).
    for tbl in _PARTITIONED:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl}_default TO app_user")


def downgrade() -> None:
    for table in _RLS_TABLES:
        for suffix, _a, _c in _POLICIES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {table}")
    op.execute("DROP TRIGGER IF EXISTS trg_workflow_versions_immutable ON workflow_versions")
    op.execute("DROP FUNCTION IF EXISTS workflow_versions_immutable()")
    op.execute("DROP FUNCTION IF EXISTS workflow_ensure_partitions(int)")
    for tbl in ["workflow_run_steps", "workflow_runs", "workflow_outbox"]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
