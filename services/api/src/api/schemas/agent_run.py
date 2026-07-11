"""Read schemas for agent runs + transcript steps."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID | None
    work_order_id: uuid.UUID | None
    parent_run_id: uuid.UUID | None
    status: str
    trigger: str
    wait_kind: str | None
    provider: str | None
    model: str | None
    label: str | None
    error: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AgentRunStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    kind: str
    name: str | None
    content: dict
    tokens: int | None
    created_at: datetime


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    tool_name: str
    arguments: dict
    status: str
    decided_at: datetime | None
    created_at: datetime


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    run_id: uuid.UUID | None
    work_order_id: uuid.UUID | None
    recipient_role: str | None
    title: str
    body: str | None
    status: str
    created_at: datetime


class UnreadCount(BaseModel):
    unread: int
