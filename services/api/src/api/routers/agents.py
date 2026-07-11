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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.agent import Agent
from api.repositories.org_provider_credential import OrgProviderCredentialRepository
from api.schemas.agent import (
    AgentCreate,
    AgentRead,
    AgentUpdate,
    ProviderCredentialSet,
    ProviderInfo,
    ProviderModelInfo,
)
from api.services.agents.llm.catalog import PROVIDERS, VALID_PROVIDERS
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
    for p in PROVIDERS:
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
    if body.provider not in VALID_PROVIDERS:
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
