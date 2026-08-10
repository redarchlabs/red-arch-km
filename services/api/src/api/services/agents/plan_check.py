"""Is this plan a plan the org can actually carry out?

The acceptance auditor (see :mod:`api.services.agents.acceptance`) catches drift at
the end, when the money is spent. This catches the same drift at the beginning, in
the one artifact that causes it: the checklist.

The failure this exists for, in full. A person filed **"Check out SEO on
redarchlabs.com and tell me what you think."** The research analyst — holding one
tool that fetches one page, and a web-search tool with no key — wrote itself this
plan:

    T1 Fetch robots.txt and sitemap(s)
    T2 Crawl up to 500 public pages (depth 3, <=2 req/s), produce CSV
    T3 Run Lighthouse for 10 pages, capture metrics/screenshots
    T4 Capture 5 screenshots of problem pages
    T5 Backlink summary using free tools
    T6 Prioritised findings report
    T7 Attach artifacts

It finished T1 in two calls. Then it spent every subsequent turn failing to start
T2, because a crawler, a Lighthouse runner, a screenshot tool and a backlink API do
not exist anywhere in this org and never did. Six runs, four of them continuations,
each one re-reading a checklist it could not advance, each ending with a polite
status summary. The stall sweeper eventually stopped picking it up — correctly, its
no-progress guard did its job — and told a person the order was stuck.

Nothing was broken. Every component behaved as designed. The plan was simply a plan
for a different organisation, and no code path anywhere had an opinion about that.

So the plan is checked against two things at the moment it is written:

* **The request.** Seven steps of crawler infrastructure is not what "tell me what
  you think" asked for, and a plan that overshoots the brief is the first move in
  the drift the acceptance auditor sees the end of.
* **The tools that exist in this org** — every tool granted to any enabled agent,
  not just the planner's own. Delegation reaches other agents; it does not conjure
  capabilities nobody has. A step that needs one of those is not a step, it is a
  wish, and the agent will discover this only by burning turns on it.

Like the auditor, it **fails open**: no key, a model error, or an unparseable reply
all let the plan through. A planner frozen out of planning is worse than a bad plan,
which the acceptance gate still gets a chance to catch.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.agent import Agent
from api.services.agents.llm.catalog import provider_for_model
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMError, LLMProvider
from api.services.agents.tools.registry import base_tool_specs

logger = logging.getLogger(__name__)

_VERDICT = re.compile(r"\b(OK|REWORK)\b")

# Tool descriptions are written for the model that calls them and can run long; the
# judge only needs to know what each one is for.
_DESCRIPTION_CHARS = 200

_SYSTEM = (
    "You are checking a work-order plan before any of it is attempted. You see the "
    "request a person filed, the checklist an agent just wrote for it, and the complete "
    "list of tools that exist in this organisation.\n\n"
    "Reject the plan for either of two reasons, and no others:\n\n"
    "1. A STEP CANNOT BE DONE. It needs a capability that is not in the tool list — "
    "crawling a whole site when the only web tool fetches one page at a time, running "
    "Lighthouse or PageSpeed, taking screenshots, reading backlink or analytics data, "
    "running arbitrary code. Agents can delegate to each other, so being someone else's "
    "job is fine; but delegation only reaches the same tool list, so a capability absent "
    "here is absent everywhere. This is the common failure and the expensive one: the "
    "agent discovers it one turn at a time, forever.\n"
    "2. THE PLAN IS NOT THE REQUEST. It is materially larger than what was asked, or it "
    "has turned a question into a construction project. 'Tell me what you think' is "
    "answered by looking and saying what you think, not by building the apparatus that "
    "would answer it exhaustively.\n\n"
    "Do NOT reject a plan for being terse, unambitious, or less rigorous than you would "
    "like. A short plan that can actually be executed is the goal. Judgement calls, "
    "reading, writing something up, and asking a person are all fine steps.\n\n"
    "Reply with exactly one line:\n"
    "OK\n"
    "or\n"
    "REWORK — <one sentence: which steps cannot be done with these tools, and what to "
    "do instead>"
)


@dataclass(frozen=True)
class PlanVerdict:
    """``ok`` is the only thing that gates. ``problem`` is what the planner is told."""

    ok: bool
    problem: str = ""
    #: False when the check did not actually run (no key, error, bad reply). The caller
    #: records this so a silent skip is never mistaken for approval.
    checked: bool = True


async def org_toolbox(session: AsyncSession, org_id: uuid.UUID, settings: Settings) -> list[str]:
    """Every capability reachable by anyone in this org, as ``name — what it does``.

    The union across enabled agents, not the planner's own grants, because the planner
    is entitled to write steps for its reports. What it is not entitled to do is write
    steps for tools that do not exist, and that is a property of the org.
    """
    specs = {s.name: s for s in base_tool_specs(settings)}
    granted: set[str] = {name for name, spec in specs.items() if spec.always_allowed}
    rows = (await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.enabled.is_(True)))).scalars().all()
    for agent in rows:
        tools = (agent.grants or {}).get("tools")
        if isinstance(tools, list):
            granted.update(str(t) for t in tools if str(t).strip())

    lines: list[str] = []
    for name in sorted(granted):
        spec = specs.get(name)
        if spec is not None:
            lines.append(f"- {name} — {spec.description[:_DESCRIPTION_CHARS]}")
        else:
            # An MCP tool or wildcard grant: the name is all this layer knows, and a
            # name is still evidence the capability exists.
            lines.append(f"- {name}")
    return lines


def _parse(reply: str) -> PlanVerdict:
    """First OK/REWORK token wins. No token at all is a non-answer, not a rejection."""
    found = _VERDICT.search(reply or "")
    if found is None:
        return PlanVerdict(ok=True, checked=False)
    if found.group(1) == "OK":
        return PlanVerdict(ok=True)
    problem = (reply[found.end() :] or "").strip(" —-:\n") or "some steps cannot be done with the available tools"
    return PlanVerdict(ok=False, problem=problem.splitlines()[0][:500])


async def check_plan(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    brief: str,
    titles: list[str],
    settings: Settings,
    model: str | None = None,
) -> PlanVerdict:
    """Can this checklist be carried out here, and is it what was asked for?"""
    chosen = model or settings.agent_acceptance_model
    provider = provider_for_model(chosen)
    key = await resolve_provider_key(session, org_id, provider, settings)
    if not key:
        logger.info("plan check skipped for org %s: no key for %s", org_id, provider)
        return PlanVerdict(ok=True, checked=False)

    tools = await org_toolbox(session, org_id, settings)
    plan = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    user = (
        f"THE REQUEST, exactly as the person wrote it:\n{brief}\n\n"
        f"----\n\nTHE PLAN just written for it:\n{plan}\n\n"
        f"----\n\nEVERY TOOL THAT EXISTS IN THIS ORGANISATION:\n" + ("\n".join(tools) or "- (none)")
    )

    try:
        result = await LLMProvider(api_key=key).complete(
            model=chosen,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            max_tokens=300,
        )
    except LLMError:
        logger.warning("plan check failed to run for org %s", org_id, exc_info=True)
        return PlanVerdict(ok=True, checked=False)
    return _parse(result.content or "")
