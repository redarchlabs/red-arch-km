"""Release / change-set promotion tables.

Phase 2 of change-management (see plan: multi-environment object promotion). An
org-admin cuts a **release** — a named, frozen snapshot of selected config — gets
it approved, and **promotes** it to a **target** (another org in this database, or
later a remote KM2 instance). Each promotion records a reverse snapshot so it can
be rolled back, and the whole history is the deployment log.

Five tenant-scoped tables, all owned by the SOURCE org (``org_id`` = the org the
release was cut from), all under the same hardened FORCE-RLS template as the rest
of the schema (migrations 002 / 028) plus the ``admin_bypass_all`` policy every
new FORCE-RLS table must carry (migration 034) — the site-admin cross-org
deployment log reads ``release_promotions`` on the bypass session.

Status columns are plain ``varchar`` + CHECK (mirroring ``workflow_versions`` /
``workflow_runs``), never a Postgres enum, so the vocabulary can evolve without a
type migration.

Revision ID: 038
Revises: 037
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass', true) = 'on'"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]

_TABLES = (
    "release_promotions",
    "release_approvals",
    "release_items",
    "releases",
    "promotion_targets",
)


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    # Cross-org bypass (mirrors migration 034): only widens visibility when a
    # privileged path opts in via the app.bypass GUC.
    op.execute(f"CREATE POLICY admin_bypass_all ON {table} USING ({_BYPASS}) WITH CHECK ({_BYPASS})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def _uuid(**kw: object) -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, **kw)


def _org_fk() -> sa.Column:
    return sa.Column(
        "org_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # ----------------------------------------------------------------- #
    # promotion_targets — where a release can be promoted to.
    # ----------------------------------------------------------------- #
    op.create_table(
        "promotion_targets",
        _uuid(),
        *_timestamps(),
        _org_fk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        # local_org:
        sa.Column("target_org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE")),
        # remote_instance:
        sa.Column("base_url", sa.String(500)),
        sa.Column("remote_org_id", postgresql.UUID(as_uuid=True)),
        # Fernet(remote api key), ORG_ENCRYPTION_KEY — mirrors WorkflowConnection; NEVER exported.
        sa.Column("secret_encrypted", sa.Text),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint("kind IN ('local_org','remote_instance')", name="ck_promotion_target_kind"),
        sa.CheckConstraint(
            "(kind = 'local_org' AND target_org_id IS NOT NULL AND base_url IS NULL)"
            " OR (kind = 'remote_instance' AND base_url IS NOT NULL AND target_org_id IS NULL)",
            name="ck_promotion_target_shape",
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_promotion_target_name"),
    )
    _apply_rls("promotion_targets")

    # ----------------------------------------------------------------- #
    # releases — an immutable frozen snapshot + governance state.
    # ----------------------------------------------------------------- #
    op.create_table(
        "releases",
        _uuid(),
        *_timestamps(),
        _org_fk(),  # = the SOURCE org
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        # The frozen bundle: inline JSONB for the config-only common case, or an
        # object-store reference for record/doc-heavy releases. Exactly one is set.
        sa.Column("bundle", postgresql.JSONB),
        sa.Column("bundle_uri", sa.Text),
        sa.Column("bundle_hash", sa.String(64)),
        sa.Column("bundle_format_version", sa.SmallInteger),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft','in_review','approved','rejected','archived')",
            name="ck_release_status",
        ),
        # A frozen release has exactly one bundle source; a draft (not yet frozen)
        # may have neither.
        sa.CheckConstraint(
            "status = 'draft' OR (bundle IS NULL) <> (bundle_uri IS NULL)",
            name="ck_release_bundle_one",
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_release_name"),
    )
    _apply_rls("releases")

    # ----------------------------------------------------------------- #
    # release_items — normalized inventory of a frozen release (per object).
    # ----------------------------------------------------------------- #
    op.create_table(
        "release_items",
        _uuid(),
        *_timestamps(),
        _org_fk(),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("natural_key", sa.String(255)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("release_id", "object_type", "lineage_id", name="uq_release_item"),
    )
    _apply_rls("release_items")

    # ----------------------------------------------------------------- #
    # release_approvals — append-only per-approver governance audit.
    # ----------------------------------------------------------------- #
    op.create_table(
        "release_approvals",
        _uuid(),
        *_timestamps(),
        _org_fk(),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "approver_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
        ),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("comment", sa.Text),
        sa.CheckConstraint("decision IN ('approved','rejected')", name="ck_release_approval_decision"),
        sa.UniqueConstraint("release_id", "approver_id", name="uq_release_approval"),
    )
    _apply_rls("release_approvals")

    # ----------------------------------------------------------------- #
    # release_promotions — one execution of a release against one target.
    # ----------------------------------------------------------------- #
    op.create_table(
        "release_promotions",
        _uuid(),
        *_timestamps(),
        _org_fk(),  # = the SOURCE org
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("promotion_targets.id", ondelete="SET NULL"),
        ),
        # Denormalized target facts so a deleted target still reads in the audit log.
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_label", sa.String(200), nullable=False),
        sa.Column("target_org_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("strategy", sa.String(16), nullable=False, server_default="skip"),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "promoted_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
        ),
        # ImportSummary (counts/warnings/errors; secret VALUES omitted).
        sa.Column("result_summary", postgresql.JSONB),
        # Reverse snapshot of the target's prior state, for rollback.
        sa.Column("pre_state_bundle", postgresql.JSONB),
        sa.Column("pre_state_uri", sa.Text),
        sa.Column("pre_state_hash", sa.String(64)),
        # On the inverse-apply record, points at the promotion being undone.
        sa.Column(
            "rollback_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_promotions.id", ondelete="SET NULL"),
        ),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True)),
        sa.Column(
            "rolled_back_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','promoting','promoted','failed','rolled_back')",
            name="ck_release_promotion_status",
        ),
    )
    op.create_index("ix_release_promotions_release", "release_promotions", ["org_id", "release_id"])
    op.create_index("ix_release_promotions_target", "release_promotions", ["org_id", "target_id"])
    _apply_rls("release_promotions")


def downgrade() -> None:
    for table in _TABLES:
        for suffix, _a, _c in _POLICIES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS admin_bypass_all ON {table}")
    op.drop_index("ix_release_promotions_target", table_name="release_promotions")
    op.drop_index("ix_release_promotions_release", table_name="release_promotions")
    for table in _TABLES:
        op.drop_table(table)
