"""Plan mode ends with a plan a person approves, and approving it starts the work.

Plan mode without an exit was a dead end: the agent researched, wrote a task list
and stopped, and the only way to act on any of it was for a person to change the
mode by hand and start the order again — leaving the agent's reasoning behind in a
finished run.

This is the shape people already know from Claude Code: work read-only, present
the plan, and let the approval be the thing that releases execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.services.agents.authority import Decision, Posture, decide
from api.services.agents.runtime import RunFinished
from api.services.agents.tools.plan_mode import SUBMIT_PLAN
from api.services.agents.tools.work_order_tasks import SET_WORK_ORDER_TASKS
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@dataclass
class _Ctx:
    session: Any
    org_id: uuid.UUID
    work_order_id: uuid.UUID | None
    agent: Any = None
    run_id: uuid.UUID | None = None
    settings: Any = None
    actor_user_id: uuid.UUID | None = None
    tool_call_id: str | None = None
    _extra: dict = field(default_factory=dict)


async def _seed(admin_session: AsyncSession):
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Plan-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="chief", provider="openai", model="gpt-5-mini", kind="coordinator", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title="Audit the site", assigned_agent_id=agent.id, mode="plan")
    wo.status = "in_progress"
    await admin_session.commit()
    return org, agent, wo, svc


async def _runs_for(session: AsyncSession, wo_id: uuid.UUID) -> list[AgentRun]:
    return list((await session.execute(select(AgentRun).where(AgentRun.work_order_id == wo_id))).scalars().all())


class TestTheGate:
    def test_submitting_a_plan_always_asks_a_person(self) -> None:
        """Even under automatic. A tool whose whole purpose is to put a decision in
        front of someone becomes a no-op if it can approve itself."""
        agent = Agent(name="c", kind="coordinator", provider="openai", model="m", grants={})

        for posture in (Posture.PLAN_ONLY, Posture.AUTOMATIC, "high_touch"):
            assert decide(agent, SUBMIT_PLAN, autonomy=posture).decision is Decision.ASK


class TestApprovingThePlan:
    async def test_it_starts_a_run_that_carries_out_the_plan(self, admin_session: AsyncSession) -> None:
        # Reaching the handler means a person already approved: the gate parks the
        # run first, and a rejection never gets here.
        org, agent, wo, svc = await _seed(admin_session)
        planning = AgentRun(
            org_id=org.id,
            agent_id=agent.id,
            work_order_id=wo.id,
            provider="openai",
            model="m",
            trigger="work_order",
            status="running",
            input={},
        )
        admin_session.add(planning)
        await admin_session.flush()
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent, run_id=planning.id)

        with pytest.raises(RunFinished):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Crawl, then report."})
        await admin_session.commit()

        queued = [r for r in await _runs_for(admin_session, wo.id) if r.status == "queued"]
        assert len(queued) == 1
        # The approved plan is the brief, not the research transcript that produced it.
        assert "Crawl, then report." in queued[0].input["task"]

    async def test_the_order_leaves_plan_mode(self, admin_session: AsyncSession) -> None:
        """Otherwise the run that was just released would be refused every tool it
        needs — approving the plan has to actually release execution."""
        org, agent, wo, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)

        with pytest.raises(RunFinished):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Do the thing."})
        await admin_session.commit()

        # 'manual', not 'automatic': approving a plan says the plan is right, not
        # that nobody needs to see what happens while it is carried out.
        assert (await svc.get_work_order(wo.id)).mode == "manual"

    async def test_the_planning_run_does_not_block_its_own_successor(self, admin_session: AsyncSession) -> None:
        """The live-run guard would otherwise see the run doing the submitting and
        skip, so approving a plan would silently start nothing."""
        org, agent, wo, svc = await _seed(admin_session)
        planning = AgentRun(
            org_id=org.id,
            agent_id=agent.id,
            work_order_id=wo.id,
            provider="openai",
            model="m",
            trigger="work_order",
            status="running",
            input={},
        )
        admin_session.add(planning)
        await admin_session.flush()
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent, run_id=planning.id)

        with pytest.raises(RunFinished):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Go."})
        await admin_session.commit()

        assert any(r.status == "queued" for r in await _runs_for(admin_session, wo.id))

    async def test_the_plan_is_written_into_the_diary(self, admin_session: AsyncSession) -> None:
        org, agent, wo, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)

        with pytest.raises(RunFinished):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Crawl every page."})
        await admin_session.commit()

        assert any("Crawl every page." in e.text for e in (await svc.list_entries_page(wo.id)).entries)

    async def test_the_task_list_survives_as_the_plan(self, admin_session: AsyncSession) -> None:
        org, agent, wo, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["Crawl", "Report"]})

        with pytest.raises(RunFinished) as raised:
            await SUBMIT_PLAN.handler(ctx, {"summary": "As listed."})

        assert [t["title"] for t in raised.value.payload["tasks"]] == ["Crawl", "Report"]


class TestRefusals:
    async def test_a_plan_with_no_summary_is_refused_without_ending_the_run(self, admin_session: AsyncSession) -> None:
        """A refusal the agent can act on — it should write the summary and retry,
        not have the run end under it."""
        org, agent, wo, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=agent)

        out = await SUBMIT_PLAN.handler(ctx, {"summary": "   "})

        assert "error" in out

    async def test_a_run_with_no_work_order_is_told_why(self, admin_session: AsyncSession) -> None:
        org, agent, _wo, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=None, agent=agent)

        out = await SUBMIT_PLAN.handler(ctx, {"summary": "Anything"})

        assert "error" in out
