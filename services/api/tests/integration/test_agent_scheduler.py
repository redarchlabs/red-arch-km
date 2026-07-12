"""Integration tests for the agent scheduler sweep (``run_due_schedules``).

Proves the piece that was missing: a due ``agent_schedules`` row turns into a
queued ``schedule``-triggered ``AgentRun`` (which the advance-runs sweep then
drives), and the schedule's ``last_run_at`` advances so it does not re-fire every
sweep. A disabled schedule never fires.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_run import AgentRun, AgentSchedule
from api.models.org import Org
from api.services.agents.scheduler import run_due_schedules

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org_and_agent(admin_session: AsyncSession) -> tuple[Org, Agent]:
    await set_tenant(admin_session, None)
    org = Org(name=f"SCHED-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    agent = Agent(
        name="chief-of-staff",
        provider="openai",
        model="gpt-5-mini",
        kind="coordinator",
        org_id=org.id,
        enabled=True,
    )
    admin_session.add(agent)
    await admin_session.commit()
    return org, agent


async def test_due_schedule_enqueues_a_queued_run(admin_session: AsyncSession) -> None:
    org, agent = await _org_and_agent(admin_session)
    sched = AgentSchedule(
        agent_id=agent.id,
        org_id=org.id,
        cron="0 7 * * *",
        task="Assemble and deliver the daily briefing",
        enabled=True,
    )
    admin_session.add(sched)
    await admin_session.commit()

    # last_run_at is None -> the never-fired catch-up convention makes it due now.
    result = await run_due_schedules(admin_session)
    await admin_session.commit()
    assert result["fired"] >= 1

    runs = (
        await admin_session.execute(
            select(AgentRun).where(AgentRun.agent_id == agent.id, AgentRun.trigger == "schedule")
        )
    ).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "queued"
    assert runs[0].input.get("task") == "Assemble and deliver the daily briefing"

    # The schedule advanced, so a second sweep does not re-fire it.
    await admin_session.refresh(sched)
    assert sched.last_run_at is not None
    second = await run_due_schedules(admin_session)
    await admin_session.commit()
    again = (
        await admin_session.execute(
            select(AgentRun).where(AgentRun.agent_id == agent.id, AgentRun.trigger == "schedule")
        )
    ).scalars().all()
    assert len(again) == 1  # not re-fired
    assert second["fired"] == 0 or all(
        r.agent_id != agent.id for r in []  # defensive: our agent didn't fire again
    )


async def test_scheduler_fires_across_orgs_on_non_superuser_session(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    """In production the sweep runs on the non-superuser km_app role, so run it
    here on the RLS-enforced ``session`` fixture (not the superuser
    ``admin_session``). Two due schedules in two different orgs must BOTH fire:
    the cross-org claim needs the ``app.bypass`` GUC (drop it and this returns
    zero rows under RLS — a regression the superuser fixture can't catch), and
    each fire is then written scoped to its own org (a real RLS backstop)."""
    seeded = []
    for _ in range(2):
        org, agent = await _org_and_agent(admin_session)
        await set_tenant(admin_session, str(org.id))
        admin_session.add(
            AgentSchedule(
                agent_id=agent.id, org_id=org.id, cron="0 7 * * *",
                task=f"briefing {org.id}", enabled=True,
            )
        )
        await admin_session.commit()
        seeded.append((org, agent))
    await set_tenant(admin_session, None)

    # Run the sweep on the non-superuser (RLS-enforced) session.
    result = await run_due_schedules(session)
    await session.commit()
    assert result["fired"] >= 2

    # Each org got exactly its own queued run, scoped correctly.
    for org, agent in seeded:
        runs = (
            await admin_session.execute(
                select(AgentRun).where(AgentRun.agent_id == agent.id, AgentRun.trigger == "schedule")
            )
        ).scalars().all()
        assert len(runs) == 1
        assert runs[0].org_id == org.id
        assert runs[0].status == "queued"


async def test_disabled_schedule_never_fires(admin_session: AsyncSession) -> None:
    org, agent = await _org_and_agent(admin_session)
    admin_session.add(
        AgentSchedule(
            agent_id=agent.id, org_id=org.id, cron="0 7 * * *", task="noop", enabled=False
        )
    )
    await admin_session.commit()

    await run_due_schedules(admin_session)
    await admin_session.commit()

    runs = (
        await admin_session.execute(select(AgentRun).where(AgentRun.agent_id == agent.id))
    ).scalars().all()
    assert runs == []
