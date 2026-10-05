"""``/api/v1/agents`` + ``/api/v1/work-orders`` — the enterprise API surface.

Authoring stays first-party (Clerk admin). A service key can list agents, trigger
an agent run (which the worker drives with the agent's configured grants), and
file / read work orders. The scope is the gate — per-resource permissions that
gate *users* do not apply to a service key.

Runs and work orders created here have no actor and no filing profile: a key is
not a person. Each is marked ``via_api_key`` with the key's id; the mark follows
delegations, consults, escalations, reviews and work-order continuations. Such a
run never searches another org and never reads with anyone's personal reach.

A **scoped key** (dimension and/or folder assignments) starts runs whose knowledge
tool reads with the key's own masks and folders — reloaded on every tool call, and
checked before the agent's ``knowledge_scope: "org"`` grant so it can never become
unrestricted — and which are limited to mask-aware tools (no workflows). A scoped
key sees only the runs and work orders it created, and lists agents as summaries
(no persona, params, grants or MCP servers). An org key's runs behave like any
unattended run, and it sees every agent, run and work order in full, as before.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.schemas.agent import AgentRead, AgentSummaryRead
from api.schemas.agent_run import AgentRunRead
from api.schemas.work_order import WorkOrderCreate, WorkOrderRead
from api.services.agents.work_order_service import WorkOrderService

router = APIRouter()


class AgentRunTrigger(BaseModel):
    task: str = Field(min_length=1, description="What the agent should do.")


@router.get("/agents", response_model=list[AgentRead] | list[AgentSummaryRead])
async def list_agents(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("agents:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[AgentRead] | list[AgentSummaryRead]:
    """Every agent in the org. A scoped key gets summaries (id, name, description,
    kind, enabled) — how an agent is built (persona, params, grants, MCP servers)
    is org-key only."""
    agents = await AgentRepository(session, principal.org_id).list_all()
    if principal.is_scoped:
        return [AgentSummaryRead.model_validate(a) for a in agents]
    return [AgentRead.model_validate(a) for a in agents]


@router.post("/agents/{agent_id}/run", response_model=AgentRunRead, status_code=status.HTTP_202_ACCEPTED)
async def trigger_agent_run(
    agent_id: uuid.UUID,
    body: AgentRunTrigger,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("agents:run"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> AgentRunRead:
    """Queue an agent run; the worker sweep drives it. Returns the run to poll."""
    agent = await AgentRepository(session, principal.org_id).get(agent_id)
    if agent is None or not agent.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found or disabled")
    run = await AgentRunRepository(session, principal.org_id).create_run(
        agent_id=agent.id,
        provider=agent.provider,
        model=agent.model,
        trigger="manual",
        input={"task": body.task},
        actor_user_id=None,  # a key is not a person; see tools/key_scope.py
        via_api_key=True,
        api_key_id=principal.api_key_id,
        status="queued",
    )
    return AgentRunRead.model_validate(run)


@router.get("/agents/runs/{run_id}", response_model=AgentRunRead)
async def get_agent_run(
    run_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("agents:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> AgentRunRead:
    run = await AgentRunRepository(session, principal.org_id).get_run(run_id)
    # A scoped key sees only the runs it started (and the runs those spawned).
    if run is None or (principal.is_scoped and run.api_key_id != principal.api_key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return AgentRunRead.model_validate(run)


@router.get("/work-orders", response_model=list[WorkOrderRead])
async def list_work_orders(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("work_orders:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[WorkOrderRead]:
    # A scoped key lists only the work orders it filed.
    items = await WorkOrderService(session, principal.org_id).list_work_orders(
        api_key_id=principal.api_key_id if principal.is_scoped else None
    )
    return [WorkOrderRead.model_validate(w) for w in items]


@router.post("/work-orders", response_model=WorkOrderRead, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    body: WorkOrderCreate,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("work_orders:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> WorkOrderRead:
    wo = await WorkOrderService(session, principal.org_id).create_work_order(
        title=body.title,
        body=body.body,
        priority=body.priority,
        assigned_agent_id=body.assigned_agent_id,
        # No filing profile: the order's runs have no actor and carry the key instead.
        created_by_profile_id=None,
        via_api_key=True,
        api_key_id=principal.api_key_id,
    )
    return WorkOrderRead.model_validate(wo)
