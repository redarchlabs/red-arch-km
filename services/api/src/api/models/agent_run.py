"""Agent execution: runs, steps (transcript), approvals, notifications, schedules.

An ``AgentRun`` is one agent session (many LLM turns). ``parent_run_id`` forms the
delegation tree; ``wait_kind`` records why a parked run is waiting (a delegated
child, a human approval, or an escalation). ``AgentRunStep`` is the durable
transcript the console tails. ``AgentApproval`` is the human "ask" gate;
``AgentNotification`` is the inbox item that surfaces bubbled escalations /
approvals; ``AgentSchedule`` is a cron trigger.

All tables are org-scoped (RLS) and carry ``org_id`` directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, UUIDMixin

AGENT_RUN_STATUSES = ("queued", "running", "waiting", "done", "error", "cancelled")
AGENT_RUN_TRIGGERS = ("manual", "work_order", "delegation", "schedule")
AGENT_WAIT_KINDS = ("delegation", "approval", "escalation")
AGENT_STEP_KINDS = (
    "user",
    "assistant",
    "thinking",
    "tool_call",
    "tool_result",
    "delegation",
    "escalation",
    "approval_required",
    "error",
)
AGENT_APPROVAL_STATUSES = ("pending", "approved", "denied")
AGENT_NOTIFICATION_KINDS = ("escalation", "approval", "review", "done")
AGENT_NOTIFICATION_STATUSES = ("unread", "read", "resolved")


class AgentRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(12), default="manual")
    wait_kind: Mapped[str | None] = mapped_column(String(12), nullable=True)

    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # The initiating task/message + any input variables.
    input: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Drives the supervisor-idle escalation backstop.
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class AgentRunStep(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # tool name, etc.
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class AgentApproval(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_approvals"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_run_steps.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(200))
    arguments: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    decided_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class AgentNotification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_notifications"

    kind: Mapped[str] = mapped_column(String(12))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True
    )
    recipient_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    recipient_role: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. "org_admin"
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="unread", index=True)
    # Which channels actually delivered: ["in_app", "email", "workflow"].
    delivered_channels: Mapped[list] = mapped_column(JSONB, default=list)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class AgentSchedule(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_schedules"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    cron: Mapped[str] = mapped_column(String(120))
    task: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
