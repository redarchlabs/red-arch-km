#!/usr/bin/env python3
"""Provision the autonomous-company agent roster into a KM2 org (idempotent).

This is the reusable **company blueprint**: departments -> a head (coordinator) +
team (operators/advisors), each agent with its kind, provider/model, capability
grants, MCP pre-authorizations, and cron schedules. Re-running creates missing
agents, updates existing ones (matched by name), wires the org chart (supervisor
links), and ensures each schedule exists.

Entities, the knowledge base, and dashboards are provisioned separately (via the
km2 MCP tools); this script owns the **agent roster** — the part the km2 MCP does
not expose.

Design notes baked in:
  * High-touch is enforced centrally (orgs.agent_autonomy), so we do NOT list
    every external tool in approval_required — the authority engine forces ASK on
    side-effecting tools automatically. Operators just get their write/execute
    grants; internal writes run freely, external actions ask the human.
  * Research roles are OPERATORS (advisory kinds cannot use external/EXECUTE MCP
    tools). Their Perplexity access is pre-authorized with a server wildcard
    "mcp__perplexity__*"; read-only search is non-side-effecting so it runs
    without approval once the server is connected + added to mcp_server_ids.

Usage:
  DATABASE_URL=postgresql+asyncpg://... \
    python -m scripts.provision_company --org-id <uuid> [--dry-run]
  (org-id defaults to the "CEO Demo" org.)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.models.agent import Agent
from api.models.agent_run import AgentSchedule
from api.schemas.agent import AgentCreate, AgentGrants, AgentUpdate
from api.services.agents.llm.catalog import provider_for_model
from api.services.agents.service import AgentConflictError, AgentService

DEFAULT_ORG_ID = "b09440d5-3dd6-4bb6-8609-25de6a4fd74e"  # "CEO Demo"

OPUS = "anthropic/claude-opus-4-8"
STD = "anthropic/claude-sonnet-5"

# Write/execute tools an operator needs (reads are always-allowed; delegation and
# escalation are role-provided for coordinators/advisors).
OP_TOOLS = ["create_record", "update_record", "create_document", "run_workflow"]

# Each blueprint row: (name, display, kind, model, supervisor, mcp[], schedules[])
# mcp labels become "mcp__<label>__*" wildcard grants (activate when the human
# connects a server of that name and adds it to the agent's mcp_server_ids).
Row = tuple

BRIEFING_TASK = (
    "Assemble and deliver the daily company briefing per the SOP: gather pending approvals, "
    "open and at-risk issues, department KPIs, new research, and escalations; write today's "
    "briefing document and delegate its email delivery to the Executive Assistant."
)

BLUEPRINT: list[Row] = [
    # Executive
    ("chief-of-staff", "Chief of Staff", "coordinator", OPUS, None, [], [("0 7 * * 1-5", BRIEFING_TASK)]),
    ("executive-assistant", "Executive Assistant", "operator", STD, "chief-of-staff", ["google"], []),
    # Marketing
    ("marketing-head", "Marketing — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("content-writer", "Content Writer", "operator", STD, "marketing-head", ["google"], []),
    ("market-research-analyst", "Market Research Analyst", "operator", STD, "marketing-head", ["perplexity"],
     [("0 8 * * 1", "Weekly market digest: research market trends and capture findings to the KB per the research SOP.")]),
    ("campaign-social-manager", "Campaign & Social Manager", "operator", STD, "marketing-head", ["slack", "google"], []),
    ("competitive-intel-analyst", "Competitive Intelligence Analyst", "operator", STD, "marketing-head", ["perplexity"],
     [("0 8 * * 1", "Weekly competitor scan: update competitor intel and capture a report to the KB.")]),
    ("brand-design", "Brand & Design", "operator", STD, "marketing-head", [], []),
    # Sales
    ("sales-head", "Sales — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("sdr-outreach", "SDR / Outreach", "operator", STD, "sales-head", ["google"], []),
    ("account-executive", "Account Executive", "operator", STD, "sales-head", ["google"], []),
    ("sales-ops-analyst", "Sales Operations Analyst", "advisory", STD, "sales-head", [], []),
    # Product
    ("product-head", "Product — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("product-manager", "Product Manager", "operator", STD, "product-head", ["notion"], []),
    ("product-ux-analyst", "Product / UX Analyst", "advisory", STD, "product-head", [], []),
    # Engineering
    ("engineering-head", "Engineering — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("engineer", "Engineer", "operator", STD, "engineering-head", ["github"], []),
    ("qa-engineer", "QA Engineer", "operator", STD, "engineering-head", [], []),
    ("code-reviewer", "Code Reviewer", "advisory", STD, "engineering-head", [], []),
    # Customer Support
    ("customer-support-head", "Customer Support — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("support-agent", "Support Agent", "operator", STD, "customer-support-head", ["google"], []),
    ("customer-success-manager", "Customer Success Manager", "operator", STD, "customer-support-head", ["google"], []),
    # Finance & Accounting
    ("finance-head", "Finance & Accounting — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("bookkeeper-accountant", "Bookkeeper / Accountant", "operator", STD, "finance-head", [], []),
    ("fpa-analyst", "FP&A Analyst", "advisory", STD, "finance-head", [], []),
    # Human Resources
    ("hr-head", "Human Resources — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("recruiter", "Recruiter", "operator", STD, "hr-head", ["google"], []),
    ("people-ops", "People Operations", "operator", STD, "hr-head", [], []),
    # Operations
    ("operations-head", "Operations — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("operations-coordinator", "Operations Coordinator", "operator", STD, "operations-head", ["slack", "google"], []),
    ("vendor-procurement", "Vendor & Procurement", "operator", STD, "operations-head", [], []),
    # Legal & Compliance
    ("legal-head", "Legal & Compliance — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("contracts-paralegal", "Contracts / Paralegal", "operator", STD, "legal-head", ["google"], []),
    ("compliance-analyst", "Compliance Analyst", "advisory", STD, "legal-head", [], []),
    # IT
    ("it-head", "IT — Head", "coordinator", STD, "chief-of-staff", [], []),
    ("systems-administrator", "Systems Administrator", "operator", STD, "it-head", ["github", "slack"], []),
    ("security-analyst", "Security Analyst", "advisory", STD, "it-head", [], []),
]

PERSONA_TAIL = (
    " Before acting, use search_knowledge to read how this company works, your department "
    "charter, and your responsibilities. Track all work as issues; capture research to the "
    "knowledge base per the SOP. Anything that leaves the company (email, posts, publishing, "
    "payments) requires the human owner's approval — prepare it fully so it can be approved at a glance."
)


def _grants(kind: str, mcp: list[str]) -> AgentGrants:
    if kind == "operator":
        tools = [*OP_TOOLS, *[f"mcp__{label}__*" for label in mcp]]
        return AgentGrants(tools=tools, records_write=True)
    # coordinators plan/delegate, advisors research/recommend: reads + role-provided
    # tools only, no write/execute grants.
    return AgentGrants(tools=[], records_write=False)


def _persona(display: str, kind: str) -> str:
    role = {
        "coordinator": "You plan and delegate; you route work to your team and escalate what you cannot resolve.",
        "advisory": "You research and recommend; you never take side-effecting actions.",
        "operator": "You carry out the hands-on work.",
    }[kind]
    return f"You are the {display}. {role}{PERSONA_TAIL}"


def _to_create(row: Row) -> AgentCreate:
    name, display, kind, model, _sup, mcp, _sched = row
    return AgentCreate(
        name=name,
        display_name=display,
        kind=kind,
        provider=provider_for_model(model),
        model=model,
        persona=_persona(display, kind),
        grants=_grants(kind, mcp),
    )


async def _ensure_schedule(session, org_id: uuid.UUID, agent_id: uuid.UUID, cron: str, task: str) -> bool:
    existing = (
        await session.execute(
            select(AgentSchedule).where(
                AgentSchedule.org_id == org_id,
                AgentSchedule.agent_id == agent_id,
                AgentSchedule.task == task,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(AgentSchedule(org_id=org_id, agent_id=agent_id, cron=cron, task=task, enabled=True))
    return True


async def provision(org_id: uuid.UUID, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] {len(BLUEPRINT)} agents for org {org_id}:")
        for name, display, kind, model, sup, mcp, sched in BLUEPRINT:
            g = _grants(kind, mcp)
            print(f"  {name:26s} {kind:11s} {model:26s} sup={sup or '-':22s} "
                  f"tools={g.tools} schedules={len(sched)}")
        return

    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = updated = skipped = sched_added = 0
    try:
        async with factory() as session:
            # Scope RLS to the target org for the whole unit of work.
            await session.execute(
                text("select set_config('app.current_tenant_id', :tid, false)"), {"tid": str(org_id)}
            )
            svc = AgentService(session, org_id)
            existing = {a.name: a for a in await svc.list_agents()}

            # Pass 1: create-or-update every agent (no supervisor yet).
            for row in BLUEPRINT:
                name = row[0]
                data = _to_create(row)
                if name in existing:
                    await svc.update_agent(
                        existing[name].id,
                        AgentUpdate(
                            display_name=data.display_name, kind=data.kind, persona=data.persona,
                            provider=data.provider, model=data.model, grants=data.grants,
                        ),
                    )
                    updated += 1
                else:
                    try:
                        await svc.create_agent(data)
                        created += 1
                    except AgentConflictError:
                        skipped += 1
            await session.flush()
            by_name = {a.name: a for a in await svc.list_agents()}

            # Pass 2: wire the org chart + schedules now that every id exists.
            for name, _display, _kind, _model, sup, _mcp, sched in BLUEPRINT:
                agent = by_name.get(name)
                if agent is None:
                    continue
                if sup and by_name.get(sup) and agent.supervisor_id != by_name[sup].id:
                    await svc.update_agent(agent.id, AgentUpdate(supervisor_id=by_name[sup].id))
                for cron, task in sched:
                    if await _ensure_schedule(session, org_id, agent.id, cron, task):
                        sched_added += 1

            await session.commit()
    finally:
        await engine.dispose()

    print(f"Provisioned org {org_id}: created={created} updated={updated} "
          f"skipped={skipped} schedules_added={sched_added}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision the autonomous-company agent roster.")
    parser.add_argument("--org-id", default=DEFAULT_ORG_ID, help="Target org UUID (default: CEO Demo).")
    parser.add_argument("--dry-run", action="store_true", help="Print the roster without touching the DB.")
    args = parser.parse_args()
    asyncio.run(provision(uuid.UUID(args.org_id), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
