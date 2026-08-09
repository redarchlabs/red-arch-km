"""Agent roster + provider-credential management — the org-admin surface behind
the new "Agents" menu section.

Authenticated the normal (Clerk / browser) way and gated to org admins. This is
the CRUD/config surface; the interactive console and run endpoints live in
``routers/agent_console.py`` and ``routers/agent_runs.py``. Provider API keys are
write-only: they are encrypted on write and never returned — the UI only learns
whether a provider is *configured*.
"""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.agent import Agent
from api.models.agent_run import AgentSchedule
from api.repositories.agent import AgentRepository
from api.repositories.org_provider_credential import OrgProviderCredentialRepository
from api.schemas.agent import (
    AgentCreate,
    AgentRead,
    AgentScheduleCreate,
    AgentScheduleRead,
    AgentScheduleUpdate,
    AgentUpdate,
    ProviderCredentialSet,
    ProviderInfo,
    ProviderModelInfo,
)
from api.services.agents.llm.catalog import providers, valid_providers
from api.services.agents.llm.keys import central_provider_key
from api.services.agents.service import (
    AgentConflictError,
    AgentError,
    AgentNotFoundError,
    AgentService,
    AgentValidationError,
)
from api.services.crypto import encrypt_secret

router = APIRouter()

_ERROR_STATUS = {
    AgentNotFoundError: status.HTTP_404_NOT_FOUND,
    AgentValidationError: status.HTTP_400_BAD_REQUEST,
    AgentConflictError: status.HTTP_409_CONFLICT,
}


def _raise_http(exc: AgentError) -> NoReturn:
    code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _to_read(agent: Agent) -> AgentRead:
    return AgentRead.model_validate(agent)


# --- provider catalog + credentials ----------------------------------------


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ProviderInfo]:
    """The provider/model catalog + whether each provider has a usable key."""
    org_creds = {c.provider for c in await OrgProviderCredentialRepository(session, ctx.org_id).list_all()}
    result: list[ProviderInfo] = []
    for p in providers():
        configured = p.name in org_creds or central_provider_key(p.name, settings) is not None
        result.append(
            ProviderInfo(
                name=p.name,
                label=p.label,
                models=[ProviderModelInfo(id=m.id, label=m.label) for m in p.models],
                key_env=p.key_env,
                configured=configured,
            )
        )
    return result


@router.post("/providers/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def set_provider_credential(
    body: ProviderCredentialSet,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Store (or replace) this org's API key for a provider, encrypted at rest."""
    if body.provider not in valid_providers():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provider: {body.provider}")
    ciphertext = encrypt_secret(body.api_key, settings.org_encryption_key.get_secret_value())
    await OrgProviderCredentialRepository(session, ctx.org_id).upsert(body.provider, ciphertext)


@router.delete("/providers/{provider}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_credential(
    provider: str,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    """Remove this org's stored key for a provider (central key, if any, remains)."""
    await OrgProviderCredentialRepository(session, ctx.org_id).delete(provider)


# --- agent CRUD ------------------------------------------------------------


@router.get("/", response_model=list[AgentRead])
async def list_agents(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgentRead]:
    agents = await AgentService(session, ctx.org_id).list_agents()
    return [_to_read(a) for a in agents]


@router.post("/", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentRead:
    try:
        agent = await AgentService(session, ctx.org_id).create_agent(body)
    except AgentError as exc:
        _raise_http(exc)
    except IntegrityError as exc:  # unique (org_id, name) race
        raise HTTPException(status.HTTP_409_CONFLICT, "An agent with that name already exists") from exc
    return _to_read(agent)


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentRead:
    try:
        agent = await AgentService(session, ctx.org_id).get_agent(agent_id)
    except AgentError as exc:
        _raise_http(exc)
    return _to_read(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentRead:
    try:
        agent = await AgentService(session, ctx.org_id).update_agent(agent_id, body)
    except AgentError as exc:
        _raise_http(exc)
    return _to_read(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    try:
        await AgentService(session, ctx.org_id).delete_agent(agent_id)
    except AgentError as exc:
        _raise_http(exc)


def _valid_cron(expr: str) -> bool:
    """Reject a malformed cron at write time.

    The sweep treats an unparseable expression as "never due", so without this a
    typo would present as an agent that silently never runs.
    """
    try:
        from croniter import croniter
    except ImportError:  # pragma: no cover - croniter ships with the API
        return True
    return bool(croniter.is_valid(expr))


# --- schedules -------------------------------------------------------------- #
# The ``agent_schedules`` table and the sweep that fires it both existed, but
# nothing exposed them: a standing instruction could only be created with direct
# database access. These routes make a schedule org configuration like the rest
# of the roster.


@router.get("/{agent_id}/schedules", response_model=list[AgentScheduleRead])
async def list_agent_schedules(
    agent_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgentSchedule]:
    await _require_agent(session, ctx.org_id, agent_id)
    result = await session.execute(
        select(AgentSchedule)
        .where(AgentSchedule.org_id == ctx.org_id, AgentSchedule.agent_id == agent_id)
        .order_by(AgentSchedule.created_at)
    )
    return list(result.scalars().all())


@router.post("/schedules", response_model=AgentScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_agent_schedule(
    body: AgentScheduleCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentSchedule:
    await _require_agent(session, ctx.org_id, body.agent_id)
    if not _valid_cron(body.cron):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid cron expression: {body.cron!r}")
    schedule = AgentSchedule(
        org_id=ctx.org_id,
        agent_id=body.agent_id,
        cron=body.cron,
        task=body.task,
        enabled=body.enabled,
    )
    session.add(schedule)
    await session.flush()
    return schedule


@router.patch("/schedules/{schedule_id}", response_model=AgentScheduleRead)
async def update_agent_schedule(
    schedule_id: uuid.UUID,
    body: AgentScheduleUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentSchedule:
    schedule = await _require_schedule(session, ctx.org_id, schedule_id)
    if body.cron is not None:
        if not _valid_cron(body.cron):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid cron expression: {body.cron!r}")
        schedule.cron = body.cron
        # The sweep recomputes due-ness from `cron` + `last_run_at`; clearing the
        # cached next firing stops a new cron inheriting the old one's schedule.
        schedule.next_run_at = None
    if body.task is not None:
        schedule.task = body.task
    if body.enabled is not None:
        schedule.enabled = body.enabled
    await session.flush()
    return schedule


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_schedule(
    schedule_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    schedule = await _require_schedule(session, ctx.org_id, schedule_id)
    await session.delete(schedule)


async def _require_agent(session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = await AgentRepository(session, org_id).get(agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


async def _require_schedule(session: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> AgentSchedule:
    result = await session.execute(
        select(AgentSchedule).where(AgentSchedule.org_id == org_id, AgentSchedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return schedule
