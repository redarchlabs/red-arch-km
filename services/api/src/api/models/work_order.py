"""Work orders — the durable unit of work an agent org executes.

A work order is filed (by a human or a trigger), assigned to a supervisor agent,
decomposed into tasks, and delegated down the org chart. Progress is tracked by
the diary (``work_order_entries``) and task status; artifacts link KM2 documents.

All four tables are org-scoped (RLS). Child tables carry ``org_id`` directly so
the per-table tenant policy applies without a join.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, UUIDMixin

# draft -> awaiting_approval -> approved -> in_progress -> done | cancelled
WORK_ORDER_STATUSES = ("draft", "awaiting_approval", "approved", "in_progress", "done", "cancelled")
# pending | in_progress | blocked | done | carried (handed to a successor WO)
WORK_ORDER_TASK_STATUSES = ("pending", "in_progress", "blocked", "done", "carried")
# How much rope the agent gets on this order. plan = think, never act; manual =
# the org posture decides what needs approval; automatic = approvals are granted
# without asking. See migration 049.
WORK_ORDER_MODES = ("plan", "manual", "automatic")


class WorkOrder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_work_order_slug_per_org"),)
    # ``updated_at`` is ``onupdate=func.now()`` — computed by the database, so after
    # an UPDATE flushes SQLAlchemy expires the attribute instead of guessing it.
    # The status and assignment routes mutate a work order and then serialize that
    # same instance, and the expired read is a lazy refresh: IO from Pydantic's
    # synchronous attribute walk, i.e. MissingGreenlet and a 500 on a transition
    # that had already committed. RETURNING fills the value in on the way out, at
    # no extra round trip on PostgreSQL.
    __mapper_args__ = {"eager_defaults": True}

    slug: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    mode: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # low|normal|high|urgent

    # The supervisor agent that owns execution (null until assigned).
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    created_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    # Successor WO when unfinished tasks roll over (reference "carried" semantics).
    rolled_over_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True
    )

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)


class WorkOrderTask(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "work_order_tasks"

    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(20))  # e.g. "T1"
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)


class WorkOrderEntry(Base, UUIDMixin, TimestampMixin):
    """A diary entry — a terse, fact-only log line from an agent's run."""

    __tablename__ = "work_order_entries"

    # ``now()`` is the *transaction* timestamp — constant across a request — so
    # several entries written together tied on it and the diary's (created_at, id)
    # keyset fell through to a random UUID. ``clock_timestamp()`` advances within
    # the transaction, which is what keeps a conversation in the order it happened.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())

    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)  # agent name at write time
    text: Mapped[str] = mapped_column(Text)

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)


class WorkOrderArtifact(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "work_order_artifacts"

    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default="output")  # input | output
    # Artifacts reuse the KM2 documents store.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
