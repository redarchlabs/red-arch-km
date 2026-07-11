"""Unit tests for MCP tool registration (namespacing, gating, SSRF, dispatch)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from api.config import Settings
from api.models.agent import Agent
from api.services.agents.mcp import client as mcp_client
from api.services.agents.mcp.client import McpError, ResolvedMcpServer, build_headers
from api.services.agents.mcp.registry import _sanitize, build_mcp_tool_specs

pytestmark = pytest.mark.unit


def _settings(**over) -> Settings:
    base = dict(secret_key=SecretStr("x"))
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_sanitize_tool_names():
    assert _sanitize("GitHub Search!") == "GitHub_Search_"


def test_build_headers_bearer_and_api_key():
    bearer = ResolvedMcpServer(id="1", name="s", transport="http", command=None, url="http://x", secret="tok")
    assert build_headers(bearer)["Authorization"] == "Bearer tok"
    apikey = ResolvedMcpServer(
        id="1", name="s", transport="http", command=None, url="http://x",
        config={"auth_type": "api_key", "header": "X-Key"}, secret="tok",
    )
    assert build_headers(apikey)["X-Key"] == "tok"


def test_ssrf_guard_blocks_literal_private_ip():
    from api.services.agents.mcp.client import _guard_url

    with pytest.raises(McpError):
        _guard_url("http://169.254.169.254/latest/meta-data", _settings())  # cloud metadata
    with pytest.raises(McpError):
        _guard_url("http://10.0.0.5/mcp", _settings())


def test_ssrf_guard_blocks_host_resolving_private(monkeypatch):
    from api.services.agents.mcp import client as mcp_mod

    monkeypatch.setattr(mcp_mod, "_resolve_ips", lambda host: ["10.1.2.3"])
    with pytest.raises(McpError):
        mcp_mod._guard_url("https://internal.example.com/mcp", _settings())


def test_ssrf_guard_allows_public_host(monkeypatch):
    from api.services.agents.mcp import client as mcp_mod

    # Any PUBLIC host is allowed (no allow-list needed) — arbitrary SaaS by design.
    monkeypatch.setattr(mcp_mod, "_resolve_ips", lambda host: ["93.184.216.34"])
    assert mcp_mod._guard_url("https://mcp.linear.app/sse", _settings()) == "https://mcp.linear.app/sse"


def test_ssrf_guard_allows_trusted_local():
    from api.services.agents.mcp.client import _guard_url

    s = _settings(workflow_trusted_local_hosts_raw="localhost")
    assert _guard_url("http://localhost:9000/mcp", s) == "http://localhost:9000/mcp"


class _StubClient:
    """Stand-in McpClient returning fixed tools and recording calls."""

    def __init__(self):
        self.calls = []

    async def list_tools(self, server):
        from api.services.agents.mcp.client import McpToolDef

        return [McpToolDef(name="search", description="Search things", input_schema={"type": "object"})]

    async def call_tool(self, server, name, arguments):
        self.calls.append((server.name, name, arguments))
        return {"text": "ok"}


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows

    def __call__(self, session, org_id):  # McpServerRepository(session, org_id)
        return self

    async def list_all(self):
        return self._rows


class _Row:
    def __init__(self, id, name):
        self.id = id
        self.name = name
        self.transport = "http"
        self.command = None
        self.url = "http://x"
        self.config = {}
        self.secret_encrypted = None
        self.enabled = True


@pytest.mark.asyncio
async def test_build_specs_namespaces_and_dispatches(monkeypatch):
    server_id = uuid4()
    agent = Agent(
        name="a", provider="openai", model="gpt-5-mini",
        mcp_server_ids=[str(server_id)],
    )
    rows = [_Row(server_id, "github")]
    # Patch the repo import inside build_mcp_tool_specs.
    import api.repositories.mcp_server as repo_mod

    monkeypatch.setattr(repo_mod, "McpServerRepository", _FakeRepo(rows))
    stub = _StubClient()

    specs = await build_mcp_tool_specs(
        session=None, org_id=uuid4(), agent=agent, settings=_settings(), client=stub
    )
    assert [s.name for s in specs] == ["mcp__github__search"]
    assert specs[0].side_effecting is True

    result = await specs[0].handler(None, {"q": "x"})
    assert result == {"text": "ok"}
    assert stub.calls == [("github", "search", {"q": "x"})]


@pytest.mark.asyncio
async def test_no_mcp_servers_returns_empty():
    agent = Agent(name="a", provider="openai", model="gpt-5-mini", mcp_server_ids=[])
    specs = await build_mcp_tool_specs(session=None, org_id=uuid4(), agent=agent, settings=_settings())
    assert specs == []
