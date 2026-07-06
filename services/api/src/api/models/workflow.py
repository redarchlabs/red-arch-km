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

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org

# Status vocabularies (kept as plain strings + CHECKs in the migration).
VERSION_STATUSES = ("draft", "published", "archived")
OUTBOX_STATUSES = ("pending", "claimed", "done", "skipped")
RUN_STATUSES = ("pending", "running", "succeeded", "failed", "skipped")
STEP_STATUSES = ("pending", "running", "succeeded", "failed", "skipped", "retrying")
OPERATIONS = ("create", "update", "delete")


class Workflow(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_workflow_name_per_org"),)

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # References workflow_versions.id. No DB FK to avoid a circular dependency
    # with workflow_versions.workflow_id; the service layer keeps it consistent.
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

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
    step_index: Mapped[int] = mapped_column(SmallInteger, default=0)
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


# Every partitioned parent needs at least one partition to accept inserts. A
# DEFAULT partition guarantees rows always land somewhere (belt-and-suspenders
# against a missing month partition) and makes create_all/tests work out of the
# box. Month partitions are added ahead of time by the maintenance job.
for _partitioned in (WorkflowOutbox, WorkflowRun, WorkflowRunStep):
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
