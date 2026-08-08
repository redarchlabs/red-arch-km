"""Read schemas for agent runs + transcript steps."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    # Workflow agent_task linkage (None for console/schedule/delegation runs).
    workflow_run_id: uuid.UUID | None = None
    workflow_node_id: str | None = None
    # The schema-validated complete_task object.
    output: dict | None = None


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
    # Populated by the list endpoint when the parked run belongs to a workflow
    # step, so the inbox can deep-link to the workflow run it is blocking
    # (/workflows/{workflow_id}/runs?run={workflow_run_id}).
    workflow_run_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None


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


class QuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    audience: str
    question: str
    context: str | None
    answer: str | None
    status: str
    answered_at: datetime | None
    created_at: datetime
    # Resolved names, so the inbox can say "Ada asks" without a second round trip.
    asked_by: str | None = None
    target_agent: str | None = None


class AnswerRequest(BaseModel):
    """A human's typed answer. Non-empty: an empty string reads to the agent as a
    real answer meaning nothing, which is worse than declining."""

    answer: str = Field(min_length=1, max_length=20_000)


class DeclineRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)


class AnswerResult(BaseModel):
    question: QuestionRead
    # False when the asking run had already ended — the answer is on the record but
    # no agent is acting on it.
    resumed: bool
