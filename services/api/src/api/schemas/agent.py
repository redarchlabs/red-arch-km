"""Agent schemas — the org-admin management contract for the agent roster.

Create/Update/Read are split (``extra="forbid"`` on inputs). Provider *credentials*
are never returned; the provider catalog + a boolean "configured" status are the
only credential-adjacent data the UI receives.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.services.agents.llm.reasoning import EFFORTS

AgentKind = Literal["coordinator", "advisory", "operator"]


def _validate_params(value: dict | None) -> dict | None:
    """Reject a ``reasoning_effort`` the API would 400 on.

    ``params`` is free-form JSON by design, so the runtime treats a bad tier as
    "use the default" rather than failing a run mid-flight. Here at the write
    boundary the admin is present to be told, so a typo is refused instead of
    silently doing something other than what was asked for.
    """
    if value is None:
        return value
    effort = value.get("reasoning_effort")
    if effort is not None and effort not in EFFORTS:
        raise ValueError(f"reasoning_effort must be one of {', '.join(EFFORTS)}")
    return value


class AgentGrants(BaseModel):
    """Capability grants for the authority layer (see services/agents/authority.py)."""

    model_config = ConfigDict(extra="forbid")

    # Named KM2 tools this agent may call (empty = only always-allowed read tools).
    tools: list[str] = Field(default_factory=list)
    # Whether the agent may create/update records (an operator-only power).
    records_write: bool = False
    # Tools that require a human approval before executing (the "ask" tier).
    approval_required: list[str] = Field(default_factory=list)


class AgentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    kind: AgentKind = "operator"
    persona: str | None = None
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)
    supervisor_id: uuid.UUID | None = None
    avatar: str | None = Field(default=None, max_length=16)
    accent: str | None = Field(default=None, max_length=16)
    enabled: bool = True
    grants: AgentGrants = Field(default_factory=AgentGrants)
    mcp_server_ids: list[uuid.UUID] = Field(default_factory=list)
    workflow_allowlist: list[uuid.UUID] = Field(default_factory=list)
    # Consent mirror of workflow_allowlist: which workflows may bind this agent
    # to an agent_task step (workflow ids as strings, or ["*"]).
    workflow_invocable: list[str] = Field(default_factory=list)

    _check_params = field_validator("params")(_validate_params)


class AgentCreate(AgentBase):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")


class AgentUpdate(BaseModel):
    """All fields optional; only provided keys are changed."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    description: str | None = None
    kind: AgentKind | None = None
    persona: str | None = None
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    params: dict | None = None
    supervisor_id: uuid.UUID | None = None
    avatar: str | None = None
    accent: str | None = None
    enabled: bool | None = None
    grants: AgentGrants | None = None
    mcp_server_ids: list[uuid.UUID] | None = None
    workflow_allowlist: list[uuid.UUID] | None = None
    workflow_invocable: list[str] | None = None

    _check_params = field_validator("params")(_validate_params)


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    display_name: str | None
    description: str | None
    kind: str
    persona: str | None
    provider: str
    model: str
    params: dict
    supervisor_id: uuid.UUID | None
    avatar: str | None
    accent: str | None
    enabled: bool
    grants: dict
    mcp_server_ids: list[uuid.UUID]
    workflow_allowlist: list[uuid.UUID]
    workflow_invocable: list[str]
    created_at: datetime
    updated_at: datetime


class AgentActivityRead(BaseModel):
    """Live state for one agent on the roster page. Only agents with something going
    on are returned, so an empty list means the whole roster is idle."""

    agent_id: uuid.UUID
    state: Literal["working", "needs_you"]
    live_runs: int
    waiting_on_you: int


# --- provider catalog + credential status ----------------------------------


class ProviderModelInfo(BaseModel):
    id: str
    label: str


class ProviderInfo(BaseModel):
    name: str
    label: str
    models: list[ProviderModelInfo]
    key_env: str
    # Whether an org key OR the central key is configured for this provider.
    configured: bool


class ProviderCredentialSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    api_key: str = Field(min_length=1)


class AgentScheduleBase(BaseModel):
    """A cron-triggered standing instruction for one agent.

    ``enabled`` defaults to **False**: creating a schedule should never start
    firing unattended work as a side effect of configuring the roster. Turn it on
    deliberately, once the agent behaves as intended when run by hand.
    """

    model_config = ConfigDict(extra="forbid")

    cron: str = Field(min_length=1, max_length=120, description="5-field cron, evaluated in UTC.")
    task: str = Field(min_length=1, description="The instruction handed to the agent on each firing.")
    enabled: bool = False


class AgentScheduleCreate(AgentScheduleBase):
    agent_id: uuid.UUID


class AgentScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cron: str | None = Field(default=None, min_length=1, max_length=120)
    task: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class AgentScheduleRead(AgentScheduleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
