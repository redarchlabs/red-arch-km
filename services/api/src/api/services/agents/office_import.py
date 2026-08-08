"""Import the Agent Office roster + charter into a KM2 org.

This ports the reference agent-office org (the ``claude_agent_office`` project's
role-agent roster and ``docs/agent-org/`` charter) onto KM2's own agent platform.
Nothing about the reference runtime comes with it: the agents run as KM2
``Agent`` rows, coordinate through KM2's delegation protocol, pause on KM2
approvals, and fire from KM2 agent schedules.

What lands:

* **The roster** — one ``Agent`` per entry in ``seeds/agent_office/roster.json``,
  carrying the persona verbatim as its system prompt.
* **The org chart** — ``supervisor_id`` wired in a second pass, since a supervisor
  is usually defined after the agents reporting to it.
* **The charter** — every ``seeds/agent_office/docs/*.md`` as a KM2 document in an
  "Agent Office" folder, so agents can ``search_knowledge`` the rules their
  personas tell them to read ("Read first, every session: charter.md, …").
* **Schedules** — a reference ``schedules`` entry becomes an ``AgentSchedule``,
  which the existing scheduler sweep fires.

Driven by ``scripts/import_agent_office.py`` (CLI) and by tests, which call
:func:`import_into` with their own session.

Idempotent — agents are upserted by name within the org, documents by key, so
re-running after editing a persona updates in place rather than duplicating.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_run import AgentSchedule
from api.models.document import Folder
from api.models.org import Org
from api.repositories.document import DocumentRepository

logger = logging.getLogger("import-agent-office")

# Seed data ships inside the package so the import works from an installed
# wheel, not just a source checkout.
SEEDS = pathlib.Path(__file__).resolve().parents[2] / "seeds" / "agent_office"
DEFAULT_ORG = "Agent Office"
FOLDER_NAME = "Agent Office Charter"

# The reference calls its executing class "implementation"; KM2's kind-gate calls
# the same class "operator" (the only class that may take side-effecting action).
KIND_MAP = {
    "implementation": "operator",
    "coordinator": "coordinator",
    "advisory": "advisory",
}

# The reference names Anthropic models directly. KM2 resolves a provider + model
# at run time, so map onto the provider and let the org's key resolution decide.
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-sonnet-5"


def _grants(entry: dict[str, Any]) -> dict[str, Any]:
    """Translate the reference's ``autoAllow`` list into KM2 capability grants.

    The reference auto-approves a set of tool names and asks the human for
    everything else; KM2 splits that into *availability* (``grants.tools``) and
    the ask tier (``grants.approval_required``). Coordinators and advisory agents
    are additionally hard-limited by the kind-gate, so this only ever widens
    within what their class already permits.
    """
    auto = [str(t) for t in (entry.get("autoAllow") or [])]
    kind = KIND_MAP.get(str(entry.get("kind") or ""), "operator")
    tools = sorted({*auto, "search_knowledge", "list_records", "get_record"})
    grants: dict[str, Any] = {"tools": tools}
    if kind == "operator":
        # Only the executing class may write, and only with the flag set.
        grants["records_write"] = True
        # Running a workflow or shelling out to Claude Code stays behind the ask
        # tier — these are the actions the reference gates on a human.
        grants["approval_required"] = ["run_workflow", "run_claude_code"]
    return grants


async def _get_or_create_org(session: AsyncSession, name: str) -> Org:
    org = (await session.execute(select(Org).where(Org.name == name))).scalar_one_or_none()
    if org is None:
        org = Org(name=name, permission_number=1)
        session.add(org)
        await session.flush()
        logger.info("created org %s (%s)", name, org.id)
    return org


async def _upsert_agents(session: AsyncSession, org: Org, roster: list[dict[str, Any]]) -> dict[str, Agent]:
    existing = {a.name: a for a in (await session.execute(select(Agent).where(Agent.org_id == org.id))).scalars().all()}
    by_name: dict[str, Agent] = {}

    for entry in roster:
        name = str(entry["name"])
        kind = KIND_MAP.get(str(entry.get("kind") or ""), "operator")
        agent = existing.get(name)
        if agent is None:
            agent = Agent(name=name, org_id=org.id, provider=DEFAULT_PROVIDER, model=DEFAULT_MODEL)
            session.add(agent)
        agent.display_name = name.replace("-", " ").title()
        agent.description = entry.get("description")
        agent.kind = kind
        agent.persona = entry.get("persona")
        agent.avatar = entry.get("avatar")
        agent.accent = entry.get("accent")
        agent.provider = DEFAULT_PROVIDER
        agent.model = DEFAULT_MODEL
        agent.grants = _grants(entry)
        agent.enabled = True
        by_name[name] = agent

    await session.flush()
    return by_name


async def _wire_org_chart(session: AsyncSession, roster: list[dict[str, Any]], agents: dict[str, Agent]) -> int:
    """Second pass: a supervisor is usually defined after its reports."""
    wired = 0
    for entry in roster:
        supervisor = entry.get("supervisor")
        agent = agents[str(entry["name"])]
        if not supervisor:
            agent.supervisor_id = None  # the apex reports to the human
            continue
        boss = agents.get(str(supervisor))
        if boss is None:
            logger.warning("%s names unknown supervisor %r — left at the apex", agent.name, supervisor)
            continue
        agent.supervisor_id = boss.id
        wired += 1
    await session.flush()
    return wired


async def _import_schedules(
    session: AsyncSession, org: Org, roster: list[dict[str, Any]], agents: dict[str, Agent]
) -> int:
    existing = {
        (s.agent_id, s.cron)
        for s in (await session.execute(select(AgentSchedule).where(AgentSchedule.org_id == org.id))).scalars().all()
    }
    created = 0
    for entry in roster:
        agent = agents[str(entry["name"])]
        for sched in entry.get("schedules") or []:
            cron = str(sched.get("cron") or "").strip()
            if not cron or (agent.id, cron) in existing:
                continue
            session.add(
                AgentSchedule(
                    org_id=org.id,
                    agent_id=agent.id,
                    cron=cron,
                    task=str(sched.get("prompt") or sched.get("task") or "Run your scheduled routine."),
                    enabled=False,  # opt-in: importing a roster must not start firing work
                )
            )
            created += 1
    await session.flush()
    return created


async def _import_docs(session: AsyncSession, org: Org) -> int:
    """Load the charter as KM2 documents the agents can search.

    The personas instruct agents to read these every session, so they have to be
    in the knowledge base rather than on disk — that is the whole point of running
    the org on KM2.
    """
    docs_dir = SEEDS / "docs"
    if not docs_dir.is_dir():
        return 0

    folder = (
        await session.execute(select(Folder).where(Folder.org_id == org.id, Folder.name == FOLDER_NAME))
    ).scalar_one_or_none()
    if folder is None:
        folder = Folder(org_id=org.id, name=FOLDER_NAME, description="Charter + protocols for the agent org")
        session.add(folder)
        await session.flush()

    repo = DocumentRepository(session, org.id)
    count = 0
    for path in sorted(docs_dir.glob("*.md")):
        key = f"agent-office/{path.stem}"
        text = path.read_text()
        title = path.stem.replace("-", " ").replace("_", " ").title()
        existing = await repo.get_by_key(key)
        if existing is not None:
            existing.title = title
            existing.text = text
            existing.folder_id = folder.id
        else:
            await repo.create(title=title, text=text, document_key=key, folder_id=folder.id)
        count += 1
    await session.flush()
    return count


async def import_into(
    session: AsyncSession, org_name: str, roster: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Import the roster + charter using a caller-supplied session.

    Split out from :func:`run` so tests (and any future API surface) can drive the
    import against their own session and transaction instead of the process-wide
    engine. Does not commit — the caller owns the transaction.
    """
    if roster is None:
        roster = load_roster()
    org = await _get_or_create_org(session, org_name)
    agents = await _upsert_agents(session, org, roster)
    wired = await _wire_org_chart(session, roster, agents)
    schedules = await _import_schedules(session, org, roster, agents)
    docs = await _import_docs(session, org)
    return {
        "agents": len(agents),
        "reporting_lines": wired,
        "schedules": schedules,
        "documents": docs,
        "org_id": org.id,
    }


def load_roster() -> list[dict[str, Any]]:
    """The vendored reference roster."""
    roster_path = SEEDS / "roster.json"
    if not roster_path.is_file():
        logger.error("no roster at %s", roster_path)
        sys.exit(1)
    return list(json.loads(roster_path.read_text())["agents"])
