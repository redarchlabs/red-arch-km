"""``/api/v1/agents`` + ``/api/v1/work-orders`` — the enterprise API surface.

Authoring stays first-party (Clerk admin). A service key can list agents, trigger
an agent run (which the worker drives with the agent's configured grants), and
file / read work orders. The scope is the gate — per-resource permissions that
gate *users* do not apply to a service key.
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
from api.schemas.agent import AgentRead
from api.schemas.agent_run import AgentRunRead
from api.schemas.work_order import WorkOrderCreate, WorkOrderRead
from api.services.agents.work_order_service import WorkOrderService

router = APIRouter()


class AgentRunTrigger(BaseModel):
    task: str = Field(min_length=1, description="What the agent should do.")


@router.get("/agents", response_model=list[AgentRead])
async def list_agents(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("agents:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[AgentRead]:
    agents = await AgentRepository(session, principal.org_id).list_all()
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
        agent_id=agent.id, provider=agent.provider, model=agent.model,
        trigger="manual", input={"task": body.task}, status="queued",
    )
    return AgentRunRead.model_validate(run)


@router.get("/agents/runs/{run_id}", response_model=AgentRunRead)
async def get_agent_run(
    run_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("agents:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> AgentRunRead:
    run = await AgentRunRepository(session, principal.org_id).get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return AgentRunRead.model_validate(run)


@router.get("/work-orders", response_model=list[WorkOrderRead])
async def list_work_orders(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("work_orders:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[WorkOrderRead]:
    items = await WorkOrderService(session, principal.org_id).list_work_orders()
    return [WorkOrderRead.model_validate(w) for w in items]


@router.post("/work-orders", response_model=WorkOrderRead, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    body: WorkOrderCreate,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("work_orders:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> WorkOrderRead:
    wo = await WorkOrderService(session, principal.org_id).create_work_order(
        title=body.title, body=body.body, priority=body.priority,
        assigned_agent_id=body.assigned_agent_id,
    )
    return WorkOrderRead.model_validate(wo)
