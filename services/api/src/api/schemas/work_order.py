"""Work-order schemas: file/list/detail + tasks + diary."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkOrderStatus = Literal[
    "draft", "awaiting_approval", "approved", "in_progress", "done", "cancelled"
]
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
