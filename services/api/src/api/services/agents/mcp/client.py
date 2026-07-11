"""MCP client — connect to an org's external MCP servers, list + call tools.

Transports (stdio / streamable-http / sse) are imported lazily from the ``mcp``
SDK so this module and its pure helpers import without the dependency. HTTP/SSE
transports pass through the same deny-by-default SSRF guard the workflow HTTP
actions use. Secrets are decrypted by the caller and held only on the transient
:class:`ResolvedMcpServer` — never logged or serialized.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from api.config import Settings
from api.services.workflow.actions import ActionError, assert_outbound_host_allowed

logger = logging.getLogger(__name__)


@dataclass
class ResolvedMcpServer:
    """A server with its secret decrypted for one client session. Never serialized."""

    id: str
    name: str
    transport: str
    command: str | None
    url: str | None
    config: dict[str, Any] = field(default_factory=dict)
    secret: str | None = None


@dataclass(frozen=True, slots=True)
class McpToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


class McpError(RuntimeError):
    """An MCP server call failed (connect / list / call / SSRF-blocked)."""


def build_headers(server: ResolvedMcpServer) -> dict[str, str]:
    """Auth + static headers for an HTTP/SSE MCP server, from config + secret."""
    headers: dict[str, str] = dict(server.config.get("headers") or {})
    auth_type = server.config.get("auth_type", "bearer" if server.secret else "none")
    if server.secret:
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {server.secret}"
        elif auth_type == "api_key":
            headers[server.config.get("header", "X-API-Key")] = server.secret
    return headers


def _guard_url(url: str | None, settings: Settings) -> str:
    if not url:
        raise McpError("MCP server has no URL")
    parsed = urlparse(url)
    try:
        assert_outbound_host_allowed(
            parsed.hostname or "",
            parsed.scheme,
            webhook_allowlist=settings.workflow_webhook_allowlist,
            trusted_local_hosts=settings.workflow_trusted_local_hosts,
            action="mcp",
        )
    except ActionError as exc:
        raise McpError(str(exc)) from exc
    return url


def _normalize_call_result(result: Any) -> dict[str, Any]:
    """Flatten an MCP CallToolResult into a compact JSON-able dict for the model."""
    blocks = getattr(result, "content", None) or []
    texts: list[str] = []
    structured: list[Any] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
        else:
            data = getattr(block, "data", None)
            if data is not None:
                structured.append(data)
    out: dict[str, Any] = {"isError": bool(getattr(result, "isError", False))}
    if texts:
        out["text"] = "\n".join(texts)
    if structured:
        out["data"] = structured
    return out


class McpClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def list_tools(self, server: ResolvedMcpServer) -> list[McpToolDef]:
        async with self._session(server) as session:
            resp = await session.list_tools()
            return [
                McpToolDef(
                    name=t.name,
                    description=t.description or "",
                    input_schema=getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
                )
                for t in resp.tools
            ]

    async def call_tool(self, server: ResolvedMcpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._session(server) as session:
            result = await session.call_tool(name, arguments)
            return _normalize_call_result(result)

    @asynccontextmanager
    async def _session(self, server: ResolvedMcpServer):
        from mcp import ClientSession  # noqa: PLC0415 - lazy heavy import

        transport = server.transport
        if transport == "stdio":
            from mcp import StdioServerParameters  # noqa: PLC0415
            from mcp.client.stdio import stdio_client  # noqa: PLC0415

            if not server.command:
                raise McpError("stdio MCP server has no command")
            args = server.config.get("args") or []
            params = StdioServerParameters(command=server.command, args=list(args), env=server.config.get("env"))
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                yield session
        elif transport in ("http", "sse"):
            url = _guard_url(server.url, self._settings)
            headers = build_headers(server)
            if transport == "http":
                from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

                async with streamablehttp_client(url, headers=headers) as (read, write, _), ClientSession(
                    read, write
                ) as session:
                    await session.initialize()
                    yield session
            else:
                from mcp.client.sse import sse_client  # noqa: PLC0415

                async with sse_client(url, headers=headers) as (read, write), ClientSession(
                    read, write
                ) as session:
                    await session.initialize()
                    yield session
        else:
            raise McpError(f"unsupported MCP transport: {transport}")
