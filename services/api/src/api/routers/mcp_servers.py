"""MCP server connection management — the org-admin surface under Agents.

Register external MCP servers the org's agents may consume. Static-secret servers
store a Fernet-encrypted bearer/api-key; OAuth servers use the browser "Connect"
flow (``/oauth/start`` → provider consent → public ``/oauth/callback``), storing
encrypted access/refresh tokens per org or per user. Secrets/tokens are never
returned — reads expose only status.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db, get_tenant_db
from api.models.mcp_server import MCP_TRANSPORTS, McpServer, McpServerUserToken
from api.repositories.mcp_server import McpServerRepository
from api.schemas.mcp_server import (
    McpOAuthStatus,
    McpPresetInfo,
    McpServerCreate,
    McpServerRead,
    McpServerUpdate,
    McpToolInfo,
    OAuthStartResponse,
)
from api.services.agents.mcp.client import McpClient, McpError
from api.services.agents.mcp.oauth_service import (
    McpOAuthError,
    McpOAuthService,
    complete_authorization,
    oauth_status,
)
from api.services.agents.mcp.presets import MCP_PRESETS
from api.services.agents.mcp.registry import resolve_for_call
from api.services.crypto import encrypt_secret

router = APIRouter()


async def _read(session: AsyncSession, org_id: uuid.UUID, server: McpServer, user_profile_id: uuid.UUID | None) -> McpServerRead:
    user_token = None
    cfg = server.config or {}
    if cfg.get("auth_type") == "oauth" and server.oauth_identity == "user" and user_profile_id is not None:
        user_token = (
            await session.execute(
                select(McpServerUserToken).where(
                    McpServerUserToken.mcp_server_id == server.id,
                    McpServerUserToken.user_profile_id == user_profile_id,
                    McpServerUserToken.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
    return McpServerRead(
        id=server.id, name=server.name, description=server.description, transport=server.transport,
        command=server.command, url=server.url, config=cfg, enabled=server.enabled,
        auth_type=cfg.get("auth_type", "none"), has_secret=server.secret_encrypted is not None,
        oauth_identity=server.oauth_identity, oauth_status=McpOAuthStatus(**oauth_status(server, user_token)),
        created_at=server.created_at,
    )


def _validate_transport(transport: str) -> None:
    if transport not in MCP_TRANSPORTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown transport: {transport}")


def _apply_auth(server: McpServer, body: McpServerCreate | McpServerUpdate, key: str) -> None:
    """Write auth_type + oauth app config + encrypted secrets onto the row."""
    cfg = dict(server.config or {})
    if body.auth_type is not None:
        cfg["auth_type"] = body.auth_type
    if body.oauth_identity is not None:
        server.oauth_identity = body.oauth_identity
    oc = dict(cfg.get("oauth") or {})
    if body.oauth_client_id is not None:
        oc["client_id"] = body.oauth_client_id
    if body.oauth_scopes is not None:
        oc["scopes"] = body.oauth_scopes
    if oc:
        cfg["oauth"] = oc
    server.config = cfg
    if body.secret is not None:
        server.secret_encrypted = encrypt_secret(body.secret, key) if body.secret else None
    if body.oauth_client_secret is not None:
        server.oauth_client_secret_encrypted = (
            encrypt_secret(body.oauth_client_secret, key) if body.oauth_client_secret else None
        )


# --- presets ---------------------------------------------------------------


@router.get("/mcp-servers/presets", response_model=list[McpPresetInfo])
async def list_presets(_ctx: Annotated[OrgContext, Depends(require_org_admin)]) -> list[McpPresetInfo]:
    return [McpPresetInfo(**asdict(p)) for p in MCP_PRESETS]


# --- OAuth callback (PUBLIC — the provider redirects the browser here) ------


@router.get("/mcp-servers/oauth/callback")
async def oauth_callback(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Unauthenticated: the random ``state`` (stored server-side) is the capability."""
    ui = settings.public_base_url.rstrip("/") + "/agents/mcp-servers"
    if error or not code or not state:
        return RedirectResponse(f"{ui}?connected=0", status_code=status.HTTP_302_FOUND)
    try:
        await complete_authorization(session, settings, state, code)
        await session.commit()
    except Exception:  # noqa: BLE001 - never leak provider/exchange details to the browser
        await session.rollback()
        return RedirectResponse(f"{ui}?connected=0", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(f"{ui}?connected=1", status_code=status.HTTP_302_FOUND)


# --- CRUD ------------------------------------------------------------------


@router.get("/mcp-servers", response_model=list[McpServerRead])
async def list_mcp_servers(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[McpServerRead]:
    servers = await McpServerRepository(session, ctx.org_id).list_all()
    return [await _read(session, ctx.org_id, s, ctx.user.profile_id) for s in servers]


@router.post("/mcp-servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    body: McpServerCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> McpServerRead:
    _validate_transport(body.transport)
    key = settings.org_encryption_key.get_secret_value()
    server = McpServer(
        name=body.name, description=body.description, transport=body.transport,
        command=body.command, url=body.url, config=dict(body.config or {}), enabled=body.enabled,
    )
    _apply_auth(server, body, key)
    try:
        server = await McpServerRepository(session, ctx.org_id).create(server)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "An MCP server with that name already exists") from exc
    return await _read(session, ctx.org_id, server, ctx.user.profile_id)


@router.patch("/mcp-servers/{server_id}", response_model=McpServerRead)
async def update_mcp_server(
    server_id: uuid.UUID,
    body: McpServerUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> McpServerRead:
    repo = McpServerRepository(session, ctx.org_id)
    server = await repo.get(server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    fields = body.model_dump(exclude_unset=True)
    for key_name in ("name", "description", "transport", "command", "url", "enabled"):
        if key_name in fields:
            if key_name == "transport" and fields[key_name] is not None:
                _validate_transport(fields[key_name])
            setattr(server, key_name, fields[key_name])
    if "config" in fields and fields["config"] is not None:
        server.config = fields["config"]
    _apply_auth(server, body, settings.org_encryption_key.get_secret_value())
    await repo.flush()
    return await _read(session, ctx.org_id, server, ctx.user.profile_id)


@router.delete("/mcp-servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    repo = McpServerRepository(session, ctx.org_id)
    server = await repo.get(server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    await repo.delete(server)


# --- OAuth connect / disconnect --------------------------------------------


@router.post("/mcp-servers/{server_id}/oauth/start", response_model=OAuthStartResponse)
async def oauth_start(
    server_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OAuthStartResponse:
    server = await McpServerRepository(session, ctx.org_id).get(server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    if (server.config or {}).get("auth_type") != "oauth":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This server does not use OAuth")
    user_pid = ctx.user.profile_id if server.oauth_identity == "user" else None
    try:
        url = await McpOAuthService(session, ctx.org_id, settings).start(server, user_pid)
    except (McpOAuthError, McpError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return OAuthStartResponse(authorization_url=url)


@router.post("/mcp-servers/{server_id}/oauth/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def oauth_disconnect(
    server_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    server = await McpServerRepository(session, ctx.org_id).get(server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    user_pid = ctx.user.profile_id if server.oauth_identity == "user" else None
    await McpOAuthService(session, ctx.org_id, settings).disconnect(server, user_pid)


@router.post("/mcp-servers/{server_id}/test", response_model=list[McpToolInfo])
async def test_mcp_server(
    server_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[McpToolInfo]:
    """Connect to the server and list its tools (connectivity + capability check)."""
    server = await McpServerRepository(session, ctx.org_id).get(server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
    resolved = await resolve_for_call(session, ctx.org_id, server, settings, ctx.user.profile_id)
    if resolved is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not connected — click Connect to authorize this server first.")
    try:
        tools = await McpClient(settings).list_tools(resolved)
    except McpError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MCP server error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - unreachable server / bad transport
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach MCP server: {exc}") from exc
    return [McpToolInfo(name=t.name, description=t.description, input_schema=t.input_schema) for t in tools]
