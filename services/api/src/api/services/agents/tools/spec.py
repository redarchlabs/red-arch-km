"""Tool spec + context: the shape every agent tool shares.

A ``ToolSpec`` is a KM2 capability the runtime can offer the model — it carries the
JSON schema the LLM sees, a governance ``category`` (used by the kind-gate), a
``side_effecting`` flag, and an async handler. ``ToolContext`` is the per-call
dependency bundle handed to every handler, mirroring the workflow engine's
``ActionContext``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from api.config import Settings
    from api.models.agent import Agent


class Category:
    """Governance categories the kind-gate reasons about (see kind_gate.py)."""

    READ = "read"  # search, list, get — always available
    WRITE = "write"  # mutate KM2 records (operator + records_write grant)
    EXECUTE = "execute"  # run a workflow / call an external MCP tool
    DELEGATE = "delegate"  # delegate_task down the org chart
    ESCALATE = "escalate"  # escalate / consult_peer / request_review
    PLAN = "plan"  # create/update work orders + tasks + diary


ToolHandler = Callable[["ToolContext", dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the function's arguments
    category: str
    handler: ToolHandler
    side_effecting: bool = False
    # Read tools every agent gets regardless of grants (still kind-gated).
    always_allowed: bool = False

    def openai_schema(self) -> dict[str, Any]:
        """Render as an OpenAI/LiteLLM function-tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolContext:
    """Everything a tool handler needs. Built once per run and reused per call."""

    session: AsyncSession
    org_id: uuid.UUID
    settings: Settings
    agent: Agent
    actor_user_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    work_order_id: uuid.UUID | None = None
    # Resolved org LLM/provider key, for tools that make their own model calls.
    extras: dict[str, Any] = field(default_factory=dict)
