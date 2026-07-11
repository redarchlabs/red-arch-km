"""Turn an agent's configured MCP servers into runtime ToolSpecs.

Tools are namespaced ``mcp__<server>__<tool>`` (sanitized to a valid tool name)
and are EXECUTE/side-effecting, so they are still subject to the agent's grants +
approval gate and its ``mcp_server_ids`` allowlist. A server that fails to list is
skipped (logged) rather than failing the whole run.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from api.config import Settings
from api.models.agent import Agent
from api.services.agents.mcp.client import McpClient, McpToolDef, ResolvedMcpServer
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec
from api.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

_SANITIZE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize(value: str) -> str:
    return _SANITIZE.sub("_", value)[:48]


def resolve_server(row: Any, settings: Settings) -> ResolvedMcpServer:
    secret = (
        decrypt_secret(row.secret_encrypted, settings.org_encryption_key.get_secret_value())
        if row.secret_encrypted
        else None
    )
    return ResolvedMcpServer(
        id=str(row.id),
        name=row.name,
        transport=row.transport,
        command=row.command,
        url=row.url,
        config=row.config or {},
        secret=secret,
    )


def _make_spec(server: ResolvedMcpServer, tool: McpToolDef, client: McpClient) -> ToolSpec:
    qualified = f"mcp__{_sanitize(server.name)}__{_sanitize(tool.name)}"

    async def handler(_ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await client.call_tool(server, tool.name, args)
        except Exception as exc:  # noqa: BLE001 - surface as tool error
            return {"error": f"MCP call failed: {exc}"}

    return ToolSpec(
        name=qualified,
        description=f"[{server.name}] {tool.description}".strip(),
        parameters=tool.input_schema or {"type": "object", "properties": {}},
        category=Category.EXECUTE,
        handler=handler,
        side_effecting=True,
    )


async def build_mcp_tool_specs(
    session,
    org_id: uuid.UUID,
    agent: Agent,
    settings: Settings,
    *,
    client: McpClient | None = None,
) -> list[ToolSpec]:
    """ToolSpecs for every tool on the agent's enabled, allow-listed MCP servers."""
    wanted = {str(x) for x in (agent.mcp_server_ids or [])}
    if not wanted:
        return []
    from api.repositories.mcp_server import McpServerRepository

    client = client or McpClient(settings)
    rows = [
        r for r in await McpServerRepository(session, org_id).list_all() if str(r.id) in wanted and r.enabled
    ]
    specs: list[ToolSpec] = []
    for row in rows:
        server = resolve_server(row, settings)
        try:
            tools = await client.list_tools(server)
        except Exception:  # noqa: BLE001 - one bad server must not kill the run
            logger.warning("MCP server %s unreachable; skipping its tools", server.name)
            continue
        specs.extend(_make_spec(server, tool, client) for tool in tools)
    return specs
