"""Agent roster service — CRUD + validation for the org's agents.

Owns the domain rules the router shouldn't: kind/provider validity, provider↔model
consistency, supervisor existence, and org-chart cycle prevention. Follows the
per-domain typed error hierarchy used by ApiKeyService / WorkflowService.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import AGENT_KINDS, Agent
from api.repositories.agent import AgentRepository
from api.schemas.agent import AgentCreate, AgentUpdate
from api.services.agents.llm.catalog import VALID_PROVIDERS, provider_for_model


class AgentError(Exception):
    """Base class for agent-domain errors."""


class AgentNotFoundError(AgentError):
    pass


class AgentValidationError(AgentError):
    pass


class AgentConflictError(AgentError):
    """A unique constraint (name-per-org) would be violated."""


def _ids_to_str(values: list[uuid.UUID] | None) -> list[str]:
    return [str(v) for v in (values or [])]


class AgentService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = AgentRepository(session, org_id)

    async def list_agents(self) -> list[Agent]:
        return await self._repo.list_all()

    async def get_agent(self, agent_id: uuid.UUID) -> Agent:
        agent = await self._repo.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"Agent {agent_id} not found")
        return agent

    async def create_agent(self, data: AgentCreate) -> Agent:
        self._validate_provider_model(data.provider, data.model)
        if data.kind not in AGENT_KINDS:
            raise AgentValidationError(f"Unknown agent kind: {data.kind}")
        if await self._repo.get_by_name(data.name) is not None:
            raise AgentConflictError(f"An agent named '{data.name}' already exists")
        if data.supervisor_id is not None:
            await self._require_agent(data.supervisor_id, role="supervisor")

        agent = Agent(
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            kind=data.kind,
            persona=data.persona,
            provider=data.provider,
            model=data.model,
            params=data.params,
            supervisor_id=data.supervisor_id,
            avatar=data.avatar,
            accent=data.accent,
            enabled=data.enabled,
            grants=data.grants.model_dump(),
            mcp_server_ids=_ids_to_str(data.mcp_server_ids),
            workflow_allowlist=_ids_to_str(data.workflow_allowlist),
        )
        return await self._repo.create(agent)

    async def update_agent(self, agent_id: uuid.UUID, data: AgentUpdate) -> Agent:
        agent = await self.get_agent(agent_id)
        fields = data.model_dump(exclude_unset=True)

        provider = fields.get("provider", agent.provider)
        model = fields.get("model", agent.model)
        if "provider" in fields or "model" in fields:
            self._validate_provider_model(provider, model)
        if "kind" in fields and fields["kind"] not in AGENT_KINDS:
            raise AgentValidationError(f"Unknown agent kind: {fields['kind']}")

        if "supervisor_id" in fields:
            new_sup = fields["supervisor_id"]
            if new_sup is not None:
                if new_sup == agent_id:
                    raise AgentValidationError("An agent cannot supervise itself")
                await self._require_agent(new_sup, role="supervisor")
                if await self._would_cycle(agent_id, new_sup):
                    raise AgentValidationError("Supervisor assignment would create a cycle")

        if "grants" in fields and fields["grants"] is not None:
            # data.grants is an AgentGrants model; model_dump already nested it.
            agent.grants = fields.pop("grants")
        if "mcp_server_ids" in fields and fields["mcp_server_ids"] is not None:
            agent.mcp_server_ids = _ids_to_str(data.mcp_server_ids)
            fields.pop("mcp_server_ids")
        if "workflow_allowlist" in fields and fields["workflow_allowlist"] is not None:
            agent.workflow_allowlist = _ids_to_str(data.workflow_allowlist)
            fields.pop("workflow_allowlist")

        for key, value in fields.items():
            setattr(agent, key, value)
        await self._repo.flush()
        return agent

    async def delete_agent(self, agent_id: uuid.UUID) -> None:
        agent = await self.get_agent(agent_id)
        await self._repo.delete(agent)

    # --- helpers -----------------------------------------------------------

    def _validate_provider_model(self, provider: str, model: str) -> None:
        if provider not in VALID_PROVIDERS:
            raise AgentValidationError(f"Unknown provider: {provider}")
        if provider_for_model(model) != provider:
            raise AgentValidationError(
                f"Model '{model}' does not belong to provider '{provider}'"
            )

    async def _require_agent(self, agent_id: uuid.UUID, *, role: str) -> Agent:
        agent = await self._repo.get(agent_id)
        if agent is None:
            raise AgentValidationError(f"Unknown {role} agent: {agent_id}")
        return agent

    async def _would_cycle(self, agent_id: uuid.UUID, new_supervisor_id: uuid.UUID) -> bool:
        """True if making new_supervisor_id report (directly/transitively) to agent_id.

        Walks up the proposed chain; if we reach agent_id, assigning it as a
        subordinate's supervisor would close a loop. Bounded by the roster size.
        """
        seen: set[uuid.UUID] = set()
        cursor: uuid.UUID | None = new_supervisor_id
        while cursor is not None:
            if cursor == agent_id:
                return True
            if cursor in seen:
                return True  # pre-existing cycle; treat defensively
            seen.add(cursor)
            node = await self._repo.get(cursor)
            cursor = node.supervisor_id if node else None
        return False
