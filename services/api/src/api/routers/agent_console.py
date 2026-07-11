"""Interactive agent console (SSE) + run history — the member-facing surface.

Open to any org member: the agent acts with the agent's configured grants, so a
member can never do more through an agent than the org admin granted it. Config
(creating/editing agents) stays admin-only in ``routers/agents.py``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.dependencies import get_tenant_db
from api.repositories.agent_run import AgentRunRepository
from api.schemas.agent_run import AgentRunRead, AgentRunStepRead
from api.services.agents.console import AgentConsoleService

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ConsoleMessage(BaseModel):
    role: str
    content: str


class ConsoleRequest(BaseModel):
    messages: list[ConsoleMessage] = Field(default_factory=list)


@router.post("/{agent_id}/console/stream")
async def agent_console_stream(
    agent_id: uuid.UUID,
    body: ConsoleRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    # Like the config assistant, avoid a request-scoped session pinned for the
    # whole SSE stream — the service opens its own short-lived session.
    factory = get_session_factory(settings)
    service = AgentConsoleService(ctx.org_id, settings, factory, ctx.user.profile_id)
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def iterator() -> AsyncGenerator[bytes]:
        try:
            async for event in service.run_stream(agent_id, history):
                yield f"data: {json.dumps(event, default=str)}\n\n".encode()
        except Exception:  # noqa: BLE001 - never break the SSE frame contract
            yield b'data: {"type": "error", "error": "Stream failed"}\n\n'

    return StreamingResponse(iterator(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/{agent_id}/runs", response_model=list[AgentRunRead])
async def list_agent_runs(
    agent_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgentRunRead]:
    runs = await AgentRunRepository(session, ctx.org_id).list_runs(agent_id=agent_id)
    return [AgentRunRead.model_validate(r) for r in runs]


@router.get("/runs/{run_id}", response_model=AgentRunRead)
async def get_agent_run(
    run_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentRunRead:
    run = await AgentRunRepository(session, ctx.org_id).get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return AgentRunRead.model_validate(run)


@router.get("/runs/{run_id}/steps", response_model=list[AgentRunStepRead])
async def get_agent_run_steps(
    run_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgentRunStepRead]:
    repo = AgentRunRepository(session, ctx.org_id)
    if await repo.get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return [AgentRunStepRead.model_validate(s) for s in await repo.list_steps(run_id)]
