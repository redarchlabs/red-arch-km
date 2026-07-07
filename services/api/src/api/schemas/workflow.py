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


class RunPermission(BaseModel):
    """Who may MANUALLY run a workflow. Org admins always may; ``mode`` widens it."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["org_admin", "any_member", "roles"] = "org_admin"
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    group_ids: list[uuid.UUID] = Field(default_factory=list)


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    run_permission: RunPermission | None = None


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
    run_permission: RunPermission = Field(default_factory=RunPermission)


class ManualRunRequest(BaseModel):
    """Run the published workflow for real against provided inputs."""

    operation: Literal["create", "update", "delete"] = "update"
    record_id: uuid.UUID | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class ManualRunResult(BaseModel):
    run_id: uuid.UUID
    status: str
    conditions_matched: bool
    actions_executed: int = 0
    error: str | None = None


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
    dead_letter: bool = False
    depth: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class WorkflowRunDetail(WorkflowRunRead):
    steps: list[WorkflowRunStepRead] = Field(default_factory=list)


class ConnectionCreate(BaseModel):
    """Create a connector credential. `secret` is write-only (encrypted at rest)."""

    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="http", max_length=32)
    base_url: str | None = Field(default=None, max_length=500)
    auth_type: Literal["none", "bearer", "api_key", "basic"] = "none"
    secret: str | None = Field(default=None, max_length=4096)
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    auth_type: Literal["none", "bearer", "api_key", "basic"] | None = None
    secret: str | None = Field(default=None, max_length=4096)
    config: dict[str, Any] | None = None


class ConnectionRead(BaseModel):
    """A connection WITHOUT its secret — only whether one is set."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    base_url: str | None
    auth_type: str
    config: dict[str, Any]
    has_secret: bool = False
