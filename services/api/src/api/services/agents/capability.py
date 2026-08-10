"""Can the chain of agents behind this work order actually do the job?

Assignment is a dropdown. Nothing checked that the agent picked — or anyone it can
hand the work to — owns a tool the job needs, so an order could be dispatched into a
chain that was guaranteed to fail and only *look* like work in progress. One real
case: "check the SEO on our website" went to a coordinator whose reachable reports
had no web tool between them. Five hours and four rounds of questions later the whole
task list was marked blocked. Every fact needed to know that at dispatch was already
in the database.

This is a warning, not a refusal. The checks are heuristics over the order's text,
and a wrong refusal would block real work; a wrong warning costs a line in the diary.

Reachability follows the same rules the runtime enforces, not the org chart drawn on
a whiteboard:

* ``delegate_task`` targets **direct reports only**, and only an agent whose kind may
  DELEGATE can use it — so the work-capable set is the closure over delegating
  agents. An advisory agent is a leaf: its own reports are unreachable through it.
* ``consult_peer`` reaches any enabled **advisory** agent org-wide, but a consult
  returns advice, not work — so peers count for "can anyone look this up" and never
  for "can anyone act".
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.agent import Agent
from api.repositories.agent import AgentRepository
from api.services.agents.authority import Decision, decide
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import Category, ToolSpec

# Kinds whose delegate_task the kind-gate permits (see kind_gate._ALLOWED_CATEGORIES).
_CAN_DELEGATE = frozenset({"coordinator", "operator"})

WEB_TOOL = "web_research"

# The other way to reach the live web: the Claude Code CLI runs with ``WebFetch`` in its
# allow-list and authenticates with the owner's subscription, so it needs no API key at
# all. It is ``EXECUTE``, so only an operator can hold it — which is exactly the fact
# worth saying out loud, since the obvious agent to hand web work to is a *researcher*,
# and an advisory researcher can never call it however the grants read.
WEB_CLI_TOOL = "run_claude_code"

# A bare URL, or a domain with a common TLD — "audit redarchlabs.com" names a target
# on the public web just as plainly as an https:// link does.
_URL = re.compile(r"https?://\S+|\bwww\.\S+|\b[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|dev|ai|co|app)\b", re.I)

# Words that only make sense about the live web. Deliberately narrow: "research" and
# "look into" are everyday words for work that never leaves the knowledge base, and
# warning on those would train people to ignore the warning.
_WEB_WORDS = re.compile(
    r"\b(seo|crawl(?:ing|ed)?|backlinks?|serp|search results?|competitors?|"
    r"web ?site|web ?page|landing page|google ranking|page ?speed)\b",
    re.I,
)


# Verbs that ask for something to *change*. An order that only wants an opinion —
# "check out our SEO and tell me what you think" — is finished by reading, so a
# chain of advisers is the right chain for it and warning would be noise.
_ACTION_WORDS = re.compile(
    r"\b(create|add|update|change|edit|fix|build|implement|deploy|publish|send|email|"
    r"file|record|schedule|book|order|migrate|rename|delete|remove|refactor|merge)\b",
    re.I,
)


def wants_web(text: str) -> bool:
    """Does this brief describe work on the live web?"""
    return bool(_URL.search(text) or _WEB_WORDS.search(text))


def wants_action(text: str) -> bool:
    """Does this brief ask for something to be changed, not just looked at?"""
    return bool(_ACTION_WORDS.search(text))


async def reachable_agents(
    session: AsyncSession,
    org_id: uuid.UUID,
    root: Agent,
) -> tuple[list[Agent], list[Agent]]:
    """``(workers, advisors)`` — who can be given this order's work, and who can be asked.

    ``workers`` is ``root`` plus the delegation closure beneath it. ``advisors`` adds
    the consultable advisory agents anywhere in the org, which a worker can reach for
    a read-only answer but cannot hand work to.
    """
    repo = AgentRepository(session, org_id)
    workers: list[Agent] = [root]
    seen = {root.id}
    frontier = [root]
    # No depth limit: the runtime does not cap how far a delegation chain runs, and a
    # checker that stopped short would report agents as unreachable that the engine
    # would happily reach — a warning about a gap that does not exist. Termination
    # comes from ``seen``, not from a counter: every agent is visited at most once, so
    # the walk is bounded by the roster and a supervisor cycle created by hand simply
    # stops when it comes back around.
    while frontier:
        following: list[Agent] = []
        for agent in frontier:
            if agent.kind not in _CAN_DELEGATE:
                continue
            for report in await repo.list_direct_reports(agent.id):
                if report.id in seen or not report.enabled:
                    continue
                seen.add(report.id)
                workers.append(report)
                following.append(report)
        frontier = following
    advisors = [a for a in await repo.list_consultable(exclude_id=root.id) if a.id not in seen]
    return workers, advisors


def _offers(agents: list[Agent], spec: ToolSpec, *, autonomy: str) -> bool:
    return any(decide(a, spec, autonomy=autonomy).decision is not Decision.DENY for a in agents)


async def capability_warnings(
    session: AsyncSession,
    org_id: uuid.UUID,
    agent: Agent,
    brief: str,
    *,
    settings: Settings | None = None,
    autonomy: str = "high_touch",
) -> list[str]:
    """What this chain is missing for this brief, in words a person can act on.

    Empty when nothing is obviously wrong — silence is the normal case, so a line in
    the diary means something.
    """
    specs = {s.name: s for s in base_tool_specs(settings)}
    workers, advisors = await reachable_agents(session, org_id, agent)
    warnings: list[str] = []

    if len(workers) == 1 and agent.kind == "coordinator":
        # A coordinator's whole job is delegation, and it is barred from acting
        # itself — so one with nobody under it can do nothing at all, whatever the
        # order says. No heuristic needed for this one.
        warnings.append(
            f"'{agent.name}' is a coordinator with no reports, so it can only plan and delegate — "
            "and there is nobody to delegate to. Give it reports, or assign this to an agent that "
            "can do the work."
        )
    elif wants_action(brief):
        can_act = any(
            decide(worker, spec, autonomy=autonomy).decision is not Decision.DENY
            for worker in workers
            for spec in specs.values()
            if spec.category in (Category.WRITE, Category.EXECUTE)
        )
        if not can_act:
            names = ", ".join(a.name for a in workers)
            warnings.append(
                f"This order asks for something to be changed, but nothing in this chain can act: "
                f"{names} have only read/plan tools between them. Assign it to an agent whose "
                "reports include an operator."
            )

    web = specs.get(WEB_TOOL)
    if web is not None and wants_web(brief):
        cli = _cli_web_route(specs, workers, autonomy=autonomy)
        if not _offers(workers + advisors, web, autonomy=autonomy):
            warnings.append(
                "This order is about the live web, but no agent it can reach has the "
                f"'{WEB_TOOL}' tool — none of them can open a page. Grant '{WEB_TOOL}' to an "
                f"agent in this chain before expecting an answer.{_cli_hint(cli)}"
            )
        elif settings is not None and not await _web_key_configured(session, org_id, settings):
            # Granted-but-keyless fails at the first call, deep inside a run, as a
            # tool error the model then reports as its own inability.
            missing = (
                f"'{WEB_TOOL}' is granted here but neither of its backends has a key for this org, "
                "so every web lookup will fail."
            )
            if cli:
                # Lead with the route that already exists. A warning that opens by
                # asking someone to go and buy something reads as "this is blocked
                # until you spend money", when the deployment can already do it.
                warnings.append(
                    f"{missing} Send this work to {', '.join(sorted(cli))} instead — they reach the "
                    f"web through '{WEB_CLI_TOOL}' on the owner's Claude subscription, no key needed. "
                    "To make it work for everyone, GEMINI_API_KEY is free (Google AI Studio, 1,500 "
                    "searches a day); ANTHROPIC_API_KEY additionally opens a named URL."
                )
            else:
                warnings.append(
                    f"{missing} GEMINI_API_KEY is free (Google AI Studio, 1,500 searches a day) and "
                    "enough for search; ANTHROPIC_API_KEY additionally opens a named URL."
                )
    return warnings


def _cli_web_route(specs: dict[str, ToolSpec], workers: list[Agent], *, autonomy: str) -> list[str]:
    """Reachable agents that could open a page through the Claude Code CLI instead.

    Absent from ``specs`` when the CLI tool is switched off for the deployment, which
    is the whole check — there is no point naming a route that does not exist.
    """
    spec = specs.get(WEB_CLI_TOOL)
    if spec is None:
        return []
    return [w.name for w in workers if decide(w, spec, autonomy=autonomy).decision is not Decision.DENY]


def _cli_hint(names: list[str]) -> str:
    """Say who *can* already do it. A warning that only names what is missing gets
    read as "buy something"; the fix here is usually just picking a different agent."""
    if not names:
        return ""
    who = ", ".join(sorted(names))
    return (
        f" No key is needed if the work goes through '{WEB_CLI_TOOL}' instead, which reaches "
        f"the web on the owner's Claude subscription — {who} can run it."
    )


async def _web_key_configured(session: AsyncSession, org_id: uuid.UUID, settings: Settings) -> bool:
    """Either backend will do — the tool falls back on its own (see web_research)."""
    from api.services.agents.llm.keys import resolve_provider_key

    for provider in ("anthropic", "gemini"):
        if await resolve_provider_key(session, org_id, provider, settings):
            return True
    return False
