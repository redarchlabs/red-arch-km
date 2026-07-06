"""Workflow schemas: definitions, versions, dry-run test, run monitoring."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entity_definition_id: uuid.UUID
    description: str | None = Field(default=None, max_length=2000)


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


class WorkflowVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_number: int
    status: str
    definition: dict[str, Any]
    published_at: datetime | None


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    entity_definition_id: uuid.UUID
    enabled: bool
    active_version_id: uuid.UUID | None


class VersionSaveRequest(BaseModel):
    """Create or fork a draft version carrying a graph definition."""

    definition: dict[str, Any] = Field(default_factory=dict)


class WorkflowTestRequest(BaseModel):
    operation: Literal["create", "update", "delete"] = "update"
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class SimulatedStep(BaseModel):
    node_id: str
    action_type: str
    simulated_output: dict[str, Any]


class ConditionTrace(BaseModel):
    node_id: str
    result: bool


class WorkflowTestResult(BaseModel):
    conditions_matched: bool
    error: str | None = None
    condition_trace: list[ConditionTrace] = Field(default_factory=list)
    steps: list[SimulatedStep] = Field(default_factory=list)


class WorkflowRunStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_id: str
    action_type: str
    step_index: int
    status: str
    attempts: int
    max_attempts: int
    next_retry_at: datetime | None
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class WorkflowRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_version_id: uuid.UUID
    trigger_operation: str
    record_id: uuid.UUID | None
    status: str
    conditions_matched: bool
    error: str | None
    depth: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class WorkflowRunDetail(WorkflowRunRead):
    steps: list[WorkflowRunStepRead] = Field(default_factory=list)
