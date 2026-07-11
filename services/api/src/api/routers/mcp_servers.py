"""MCP server connection management — the org-admin surface under Agents.

Register external MCP servers the org's agents may consume. Secrets are encrypted
on write and never returned; a ``POST /{id}/test`` lists the server's tools so an
admin can confirm connectivity before granting it to an agent.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.mcp_server import MCP_TRANSPORTS, McpServer
from api.repositories.mcp_server import McpServerRepository
from api.schemas.mcp_server import (
    McpServerCreate,
    McpServerRead,
    McpServerUpdate,
    McpToolInfo,
)
from api.services.agents.mcp.client import McpClient, McpError
from api.services.agents.mcp.registry import resolve_server
from api.services.crypto import encrypt_secret

router = APIRouter()


def _to_read(server: McpServer) -> McpServerRead:
    return McpServerRead(
        id=server.id,
        name=server.name,
        description=server.description,
        transport=server.transport,
        command=server.command,
        url=server.url,
        config=server.config or {},
        enabled=server.enabled,
        has_secret=server.secret_encrypted is not None,
        created_at=server.created_at,
    )


def _validate_transport(transport: str) -> None:
    if transport not in MCP_TRANSPORTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown transport: {transport}")


@router.get("/mcp-servers", response_model=list[McpServerRead])
async def list_mcp_servers(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[McpServerRead]:
    servers = await McpServerRepository(session, ctx.org_id).list_all()
    return [_to_read(s) for s in servers]


@router.post("/mcp-servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    body: McpServerCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> McpServerRead:
    _validate_transport(body.transport)
    encrypted = (
        encrypt_secret(body.secret, settings.org_encryption_key.get_secret_value())
        if body.secret
        else None
    )
    server = McpServer(
        name=body.name, description=body.description, transport=body.transport,
        command=body.command, url=body.url, config=body.config, secret_encrypted=encrypted,
        enabled=body.enabled,
    )
    try:
        server = await McpServerRepository(session, ctx.org_id).create(server)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "An MCP server with that name already exists") from exc
    return _to_read(server)


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
    if "transport" in fields and fields["transport"] is not None:
        _validate_transport(fields["transport"])
    if "secret" in fields:
        secret = fields.pop("secret")
        server.secret_encrypted = (
            encrypt_secret(secret, settings.org_encryption_key.get_secret_value()) if secret else None
        )
    for key, value in fields.items():
        setattr(server, key, value)
    await repo.flush()
    return _to_read(server)


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
    resolved = resolve_server(server, settings)
    try:
        tools = await McpClient(settings).list_tools(resolved)
    except McpError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MCP server error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - unreachable server / bad transport
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach MCP server: {exc}") from exc
    return [McpToolInfo(name=t.name, description=t.description, input_schema=t.input_schema) for t in tools]
