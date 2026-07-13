"""Assemble the base KM2 tool set available to the agent runtime.

Delegation / work-order / MCP tools are registered here as they land (steps 4–5);
the runtime filters this list through the authority engine per agent, so listing a
tool here does NOT grant it — grants + kind-gate still decide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.services.agents.tools.batch_generate import BATCH_GENERATE, CHECK_BATCH
from api.services.agents.tools.claude_code import RUN_CLAUDE_CODE
from api.services.agents.tools.documents import CREATE_DOCUMENT
from api.services.agents.tools.knowledge import SEARCH_KNOWLEDGE
from api.services.agents.tools.records import (
    CREATE_RECORD,
    GET_RECORD,
    LIST_RECORDS,
    UPDATE_RECORD,
)
from api.services.agents.tools.spec import ToolSpec
from api.services.agents.tools.web_research import WEB_RESEARCH
from api.services.agents.tools.workflows import LIST_WORKFLOWS, RUN_WORKFLOW

if TYPE_CHECKING:
    from api.config import Settings


def base_tool_specs(settings: Settings | None = None) -> list[ToolSpec]:
    """The always-registered KM2 tools (before per-agent authority filtering).

    Read tools (``search_knowledge``, ``list_records``, ``get_record``,
    ``list_workflows``) are available to every agent; the write/execute tools are
    listed here but the authority engine (kind-gate + grants) decides per agent
    whether they are actually offered.

    ``settings`` is optional so callers without config (e.g. unit tests) still get the
    stable base set; it gates opt-in tools like ``run_claude_code``.
    """
    specs = [
        SEARCH_KNOWLEDGE,
        LIST_RECORDS,
        GET_RECORD,
        CREATE_RECORD,
        UPDATE_RECORD,
        CREATE_DOCUMENT,
        LIST_WORKFLOWS,
        RUN_WORKFLOW,
        WEB_RESEARCH,
        BATCH_GENERATE,
        CHECK_BATCH,
    ]
    # Powerful local-exec tool: registered only when explicitly enabled, and even then
    # only ever *offered* to an agent that also holds the run_claude_code grant.
    if settings is not None and settings.enable_claude_cli_tool:
        specs.append(RUN_CLAUDE_CODE)
    return specs
