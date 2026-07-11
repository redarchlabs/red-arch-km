"""Assemble the base KM2 tool set available to the agent runtime.

Delegation / work-order / MCP tools are registered here as they land (steps 4–5);
the runtime filters this list through the authority engine per agent, so listing a
tool here does NOT grant it — grants + kind-gate still decide.
"""

from __future__ import annotations

from api.services.agents.tools.knowledge import SEARCH_KNOWLEDGE
from api.services.agents.tools.spec import ToolSpec
from api.services.agents.tools.workflows import LIST_WORKFLOWS, RUN_WORKFLOW


def base_tool_specs() -> list[ToolSpec]:
    """The always-registered KM2 tools (before per-agent authority filtering)."""
    return [SEARCH_KNOWLEDGE, LIST_WORKFLOWS, RUN_WORKFLOW]
