"""Work-order schemas: file/list/detail + tasks + diary."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkOrderStatus = Literal["draft", "awaiting_approval", "approved", "in_progress", "done", "cancelled"]
Priority = Literal["low", "normal", "high", "urgent"]


class WorkOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    body: str | None = None
    priority: Priority = "normal"
    assigned_agent_id: uuid.UUID | None = None


class WorkOrderStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorkOrderStatus


class WorkOrderAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_agent_id: uuid.UUID | None = None


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    title: str = Field(min_length=1, max_length=300)
    status: Literal["pending", "in_progress", "blocked", "done", "carried"] = "pending"
    sort_order: int = 0
    assigned_agent_id: uuid.UUID | None = None


class TasksSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskInput] = Field(default_factory=list)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    title: str
    status: str
    sort_order: int
    assigned_agent_id: uuid.UUID | None


class EntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    role: str | None
    text: str
    created_at: datetime


class WorkOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    status: str
    body: str | None
    priority: str
    assigned_agent_id: uuid.UUID | None
    created_by_profile_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class WorkOrderDetail(WorkOrderRead):
    tasks: list[TaskRead] = Field(default_factory=list)
    entries: list[EntryRead] = Field(default_factory=list)
    progress: float = 0.0


class MapLane(BaseModel):
    """One participant's horizontal track. ``key`` is the agent id, or the literal
    ``"human"`` for the lane that questions and approvals land in — a person is a
    participant in this work, and hiding them makes a blocked order look idle."""

    key: str
    label: str
    avatar: str | None = None
    agent_kind: str | None = None
    # Rolled up from the lane's runs: the worst live state wins, so a lane that is
    # blocked reads as blocked even if it also finished something earlier.
    status: str | None = None


# `consulted`/`asked` block the asker until an answer arrives; `delegated` does
# not. `blocked` is a person owing a decision — the only kind you can act on.
EventKind = Literal["started", "delegated", "consulted", "answered", "blocked", "finished", "failed", "note"]


class MapEvent(BaseModel):
    id: str
    lane: str
    kind: EventKind
    at: datetime
    title: str
    detail: str | None = None
    # Where a cross-lane arrow lands: the consulted peer, the delegate, or the
    # human being asked.
    target_lane: str | None = None
    run_id: uuid.UUID | None = None
    # Set on a `blocked` event that a person can clear, so the page can offer the
    # decision where the block is visible rather than only in the inbox.
    approval_id: uuid.UUID | None = None


class EntryPageRead(BaseModel):
    """A slice of diary, oldest-first, with whether older entries remain."""

    entries: list[EntryRead] = Field(default_factory=list)
    has_more: bool = False


class WorkOrderMap(BaseModel):
    lanes: list[MapLane] = Field(default_factory=list)
    events: list[MapEvent] = Field(default_factory=list)
