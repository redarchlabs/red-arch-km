"""Agent roster model — the org-scoped, provider-agnostic port of the reference
``RoleAgent``.

One row per agent in an org's org-chart. ``supervisor_id`` is a self-FK that *is*
the org chart (null = top of chain). ``kind`` is the governance class enforced by
the kind-gate (coordinator plans + delegates only; advisory reads + advises;
operator executes). Capability grants, MCP-server references and the workflow
allowlist are JSONB so the authority layer can evolve without a migration.

Provider credentials are NOT stored here — the agent only names a ``provider`` +
``model``; the key is resolved at run time (org key ?? central) by
``api.services.agents.llm.keys.resolve_provider_key``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, LineageMixin, TimestampMixin, UUIDMixin

# Governance classes (kind-gate). Kept as strings + comment, matching the
# codebase convention (workflow statuses are plain strings, not PG enums).
AGENT_KINDS = ("coordinator", "advisory", "operator")


class Agent(Base, UUIDMixin, TimestampMixin, LineageMixin):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_agent_name_per_org"),)

    name: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="operator")
    # System prompt / persona appended to the runtime's base instructions.
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LiteLLM provider + model id (see services/agents/llm/catalog.py).
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    # The model this agent uses WHEN REVIEWING someone else's work. Reading a plan
    # is far cheaper than writing one, so a board can sit on a mini model while its
    # authors run on something larger. NULL falls back to ``model``.
    review_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Sampling / call params: {"temperature": 0.2, "max_tokens": 2048, ...}.
    params: Mapped[dict] = mapped_column(JSONB, default=dict)

    # The org chart: this agent escalates to supervisor_id (null = apex).
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    avatar: Mapped[str | None] = mapped_column(String(16), nullable=True)
    accent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Authority: capability grants (see services/agents/authority.py). Shape:
    # {"tools": ["search_knowledge", ...], "records_write": bool,
    #  "approval_required": ["run_workflow", ...]}.
    grants: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Subset of the org's mcp_servers this agent may use (list of ids as str).
    mcp_server_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # Specific workflow ids this agent may run (list of ids as str); empty = none.
    workflow_allowlist: Mapped[list] = mapped_column(JSONB, default=list)
    # Consent mirror of workflow_allowlist: which workflows may bind THIS agent to
    # an agent_task node (list of workflow ids as str, or ["*"]). Empty = none —
    # a workflow author cannot borrow an agent's authority without the agent
    # (admin) opting in on the agent side too.
    workflow_invocable: Mapped[list] = mapped_column(JSONB, default=list)

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
