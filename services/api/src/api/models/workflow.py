"""Workflow-engine models.

Two static tables (``workflows``, ``workflow_versions``) plus three high-volume,
RANGE-partitioned tables (``workflow_outbox``, ``workflow_runs``,
``workflow_run_steps``). The partitioned tables carry a composite PK
``(id, created_at)`` because the partition key must be part of every unique
constraint.

Each partitioned parent gets a ``*_default`` partition created via an
``after_create`` DDL hook, so both ``Base.metadata.create_all`` (tests) and the
Alembic migration produce immediately-usable tables; the maintenance job adds
month partitions ahead of time for performance. The workflow ``definition`` is a
node graph (nodes + edges) authored in the drag/drop designer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, LineageMixin, TimestampMixin, UUIDMixin
from api.models.org import Org

# Status vocabularies (kept as plain strings + CHECKs in the migration).
VERSION_STATUSES = ("draft", "published", "archived")
OUTBOX_STATUSES = ("pending", "claimed", "done", "skipped")
# "waiting" is a run parked at a delay node until its resume_at elapses.
RUN_STATUSES = ("pending", "running", "waiting", "succeeded", "failed", "skipped")
STEP_STATUSES = ("pending", "running", "succeeded", "failed", "skipped", "retrying")
OPERATIONS = ("create", "update", "delete")
# Where an outbox change originated. "record" = an ordinary edit; "form" = a
# public intake-form submission (lets a trigger fire only on form submissions).
# "webhook" = an inbound-endpoint call (see the integrations phase).
OUTBOX_SOURCES = ("record", "form", "webhook")

# Token lifecycle for the BPMN token engine (schema_version 2 graphs). A run has
# one or more tokens; each is a durable execution cursor advanced one node at a
# time. ``waiting`` tokens are parked (timer/join/user/receive/retry/…) until a
# sweep reactivates them; ``dead`` = error-killed, terminate-killed, or absorbed
# by a join. ``running`` carries a lease so a crashed advance is re-queued.
TOKEN_STATUSES = ("active", "running", "waiting", "completed", "dead")
# Why a token is parked (the sweep/correlation path that will wake it).
TOKEN_WAIT_KINDS = (
    "timer",
    "user_task",
    "receive",
    "join",
    "boundary",
    "subprocess",
    "event_based",
    "retry",
)


class Workflow(Base, UUIDMixin, TimestampMixin, LineageMixin):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_workflow_name_per_org"),
        # Hot-path index for the per-record-write inline-dispatch EXISTS check
        # (has_inline_for_entity). Partial: only inline-flagged rows are indexed.
        Index(
            "ix_workflows_inline_entity",
            "org_id",
            "entity_definition_id",
            postgresql_where=text("enabled AND run_inline_on_change"),
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL for a manual (BPMN "none" start event) workflow that is run on demand
    # with caller-supplied input variables rather than bound to an entity's
    # create/update/delete stream. Data-change and form-source triggers require it.
    entity_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), index=True, nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # When true, an entity-change trigger runs INLINE in the request that mutated
    # the record (right after the write) instead of waiting for the beat sweep —
    # for latency-sensitive reactions (e.g. a robot announcing a state change).
    # The record's outbox row is still written, so the sweep dedups the inline run
    # and still fires any non-inline workflows on the same change.
    run_inline_on_change: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # References workflow_versions.id. No DB FK to avoid a circular dependency
    # with workflow_versions.workflow_id; the service layer keeps it consistent.
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Who may MANUALLY run this workflow. Org admins always may; ``mode`` can
    # additionally allow any member or specific roles/groups. See can_run().
    run_permission: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=lambda: {"mode": "org_admin"}, server_default='{"mode": "org_admin"}'
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()


class WorkflowVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version_number"),)

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class WorkflowOutbox(Base):
    """Transactional change log written by DynamicEntityRepository in the same
    tx as the record mutation. Swept by the dispatcher."""

    __tablename__ = "workflow_outbox"
    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at"),
        Index("ix_wf_outbox_pending", "seq", postgresql_where=text("status = 'pending'")),
        Index(
            "ix_wf_outbox_entity",
            "org_id",
            "entity_definition_id",
            postgresql_where=text("status = 'pending'"),
        ),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False))
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE")
    )
    entity_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    entity_table: Mapped[str] = mapped_column(String(63))
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    operation: Mapped[str] = mapped_column(String(10))
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    origin_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # "record" (ordinary edit) or "form" (public intake-form submission).
    source: Mapped[str] = mapped_column(String(20), default="record", server_default="record")
    dedup_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)


class WorkflowRun(Base):
    """One run per (outbox event × workflow). Audit + monitoring source."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at"),
        UniqueConstraint("org_id", "outbox_id", "workflow_id", "created_at", name="uq_wf_run_event"),
        Index("ix_wf_runs_workflow", "org_id", "workflow_id", "created_at"),
        # Drives the run-timers sweep for delayed runs due to resume.
        Index("ix_wf_runs_waiting", "resume_at", postgresql_where=text("status = 'waiting'")),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    outbox_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    outbox_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trigger_operation: Mapped[str] = mapped_column(String(10))
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    conditions_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    depth: Mapped[int] = mapped_column(SmallInteger, default=0)
    # Delay/wait support (legacy walker): a "waiting" run resumes from
    # resume_node_id once resume_at elapses. The token engine uses per-token
    # waits instead (workflow_run_tokens), leaving these NULL.
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- token engine (schema_version 2) ---
    # Run-scoped variables accumulated across steps; written via jsonb_set under
    # the per-run advisory lock so parallel tokens setting different keys don't
    # clobber. Expression context = {before, after, vars, steps, run, token}.
    variables: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    # Monotonic per-run step counter (allocated under the run advisory lock) —
    # gives deterministic step_index ordering even with concurrent tokens, and
    # bounds a run against MAX_RUN_STEPS regardless of branch fan-out.
    step_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Call-activity / sub-process lineage (a child run spawned by a parent token).
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parent_token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Set when the run terminated with an uncaught error that exhausted retries
    # and hit no catcher — surfaced in the dead-letter queue for manual replay.
    dead_letter: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )


class WorkflowRunStep(Base):
    """Per-action-node execution log; drives the run-monitoring step trace."""

    __tablename__ = "workflow_run_steps"
    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at"),
        Index("ix_wf_steps_run", "org_id", "run_id", "step_index"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    node_id: Mapped[str] = mapped_column(String(64))
    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_type: Mapped[str] = mapped_column(String(64))
    # The token that produced this step (token engine). NULL for legacy-walker
    # runs. Links the flat step trace to the branch it ran on.
    token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Widened smallint -> int: parallel fan-out + loops can exceed 32k in theory,
    # and the run-wide step budget lives well below that.
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WorkflowRunToken(Base):
    """A durable execution cursor within a run (BPMN token engine).

    Multiple active tokens = parallel branches. The engine claims ``active``
    tokens (``FOR UPDATE SKIP LOCKED``, ordered by ``seq``), leases them
    ``running``, advances each one node, then re-parks (``waiting``) or completes
    them. Parking replaces the legacy run-level ``resume_node_id`` with a
    per-token cursor, so timers, joins, user/receive tasks, retries and boundary
    events are all the same durable park-and-resume the ``delay`` node proved.

    Partitioned like the rest, with ``created_at`` pinned to the owning run's
    ``created_at`` so a run's tokens co-locate in one monthly partition and every
    join/settle query stays partition-local.
    """

    __tablename__ = "workflow_run_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at"),
        # Claim scan: ORDER BY seq over just the active tokens (Merge Append).
        Index("ix_wf_tokens_active", "seq", postgresql_where=text("status = 'active'")),
        # Timer sweep: due parked tokens (timer / boundary timer / retry).
        Index(
            "ix_wf_tokens_timer",
            "resume_at",
            postgresql_where=text("status = 'waiting' AND wait_kind IN ('timer','boundary','retry')"),
        ),
        # Correlation sweep: wake a receive/user token by its correlation key.
        Index(
            "ix_wf_tokens_correlation",
            "org_id",
            "correlation_key",
            postgresql_where=text("status = 'waiting' AND correlation_key IS NOT NULL"),
        ),
        # Crash reaper: reclaim tokens leased 'running' past the lease TTL.
        Index("ix_wf_tokens_stuck", "leased_at", postgresql_where=text("status = 'running'")),
        Index("ix_wf_tokens_run", "org_id", "run_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False))
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    run_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The node this token sits at / will execute next.
    node_id: Mapped[str] = mapped_column(String(64))
    # The edge this token arrived by — needed for AND/OR join edge-counting.
    arrived_from_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arrived_via_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")
    wait_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Fork / sub-process lineage.
    parent_token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(SmallInteger, default=0)
    # Crash-recovery lease held while status='running'.
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Token-local scratch (loop counters, buffered error payload, boundary ctx).
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


CONNECTION_AUTH_TYPES = ("none", "bearer", "api_key", "basic")


class WorkflowConnection(Base, UUIDMixin, TimestampMixin, LineageMixin):
    """A reusable, org-scoped credential for the ``http_request`` connector task.

    The secret (bearer token / api key / basic password) is stored Fernet-encrypted
    (services/crypto.py, keyed by ORG_ENCRYPTION_KEY) and is decrypted only at
    execute time — it never rides the definition, a step output, an input snapshot,
    or a log. FORCE RLS keeps a connection strictly inside its org.
    """

    __tablename__ = "workflow_connections"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_workflow_connection_name_per_org"),)

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), default="http", server_default="http")
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_type: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    # Fernet ciphertext of the secret credential; NULL for auth_type 'none'.
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-secret auth/config: api-key header name, basic username, static headers.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()


class WorkflowInboundEndpoint(Base, UUIDMixin, TimestampMixin, LineageMixin):
    """A public webhook URL that starts a workflow run when POSTed to.

    The URL carries an opaque token; only its SHA-256 hash is stored (like intake
    form links), so a leaked DB row can't be replayed. The public receiver looks
    the endpoint up by hash on a privileged session, then downgrades to the
    endpoint's org for the RLS-scoped run creation.
    """

    __tablename__ = "workflow_inbound_endpoints"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_wf_inbound_token_hash"),)

    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Fernet ciphertext of the HMAC signing secret (services/crypto.py). Present
    # ⇒ the receiver REQUIRES a valid X-KM2-Signature; NULL ⇒ legacy token-only.
    signing_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()


# Every partitioned parent needs at least one partition to accept inserts. A
# DEFAULT partition guarantees rows always land somewhere (belt-and-suspenders
# against a missing month partition) and makes create_all/tests work out of the
# box. Month partitions are added ahead of time by the maintenance job.
for _partitioned in (WorkflowOutbox, WorkflowRun, WorkflowRunStep, WorkflowRunToken):
    _name = _partitioned.__tablename__
    event.listen(
        _partitioned.__table__,
        "after_create",
        DDL(f"CREATE TABLE IF NOT EXISTS {_name}_default PARTITION OF {_name} DEFAULT"),
    )
    event.listen(
        _partitioned.__table__,
        "before_drop",
        DDL(f"DROP TABLE IF EXISTS {_name}_default"),
    )
