"""Assemble the full tool set for one agent run: base KM2 tools + MCP tools.

Kept separate from ``registry.base_tool_specs`` because MCP loading touches the DB
+ network, while the base set is pure. The runtime applies authority filtering to
whatever this returns, so listing a tool here never grants it.
"""

from __future__ import annotations

import uuid

from api.config import Settings
from api.models.agent import Agent
from api.services.agents.delegation import delegation_tool_specs
from api.services.agents.mcp.registry import build_mcp_tool_specs
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import ToolSpec


async def load_agent_tools(
    session,
    org_id: uuid.UUID,
    agent: Agent,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> list[ToolSpec]:
    # base KM2 tools + coordination primitives (kind-gated) + this agent's MCP tools
    specs = [*base_tool_specs(settings), *delegation_tool_specs()]
    specs.extend(await build_mcp_tool_specs(session, org_id, agent, settings, actor_user_id=actor_user_id))
    return specs
