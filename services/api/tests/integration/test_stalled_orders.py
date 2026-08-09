"""An order whose agents have all stopped, with the work unfinished.

A run's ``done`` only ever meant the model stopped calling tools. Seen live: an
agent read the task list, asked three questions, wrote a paragraph and stopped,
leaving five of six tasks open — and the order sat ``in_progress`` at 17% looking
exactly like one being worked. Every other stall in this system surfaces; this one
was invisible, which made it the easiest to lose.
"""

from __future__ import annotations

import uuid

import pytest
from api.config import get_settings
from api.models.agent import Agent
from api.models.agent_run import AgentNotification, AgentRun
from api.models.org import Org
from api.services.agents.run_executor import AgentRunExecutor
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession, *, tasks: list[str], run_status: str = "done"):
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Stall-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="worker", provider="openai", model="m", kind="operator", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title="Ship the thing", assigned_agent_id=agent.id)
    wo.status = "in_progress"
    if tasks:
        await svc.set_tasks(wo.id, [{"title": t, "sort_order": i} for i, t in enumerate(tasks)])
    if run_status:
        admin_session.add(
            AgentRun(
                org_id=org.id,
                agent_id=agent.id,
                work_order_id=wo.id,
                provider="openai",
                model="m",
                trigger="work_order",
                status=run_status,
                input={},
            )
        )
    await admin_session.commit()
    return org, wo, svc


async def _notices(session: AsyncSession, org_id) -> list[AgentNotification]:
    rows = (await session.execute(select(AgentNotification).where(AgentNotification.org_id == org_id))).scalars().all()
    return list(rows)


def _executor() -> AgentRunExecutor:
    return AgentRunExecutor(get_settings())


class TestItFindsTheStall:
    async def test_an_order_whose_agents_all_stopped_is_reported(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=["Crawl", "Report"])

        found = await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert found == 1
        assert any("stopped with work outstanding" in n.title for n in await _notices(admin_session, org.id))

    async def test_it_says_how_much_is_left(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=["A", "B", "C"])
        tasks = await svc.list_tasks(wo.id)
        tasks[0].status = "done"
        await svc.flush_tasks()
        await admin_session.commit()

        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        body = (await _notices(admin_session, org.id))[0].body
        assert "2 of 3" in body and "1 done" in body

    async def test_the_stall_is_written_into_the_diary(self, admin_session: AsyncSession) -> None:
        # The order's own record is where anyone reconstructing this will look.
        org, wo, svc = await _seed(admin_session, tasks=["A"])

        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert any("Stalled" in e.text for e in (await svc.list_entries_page(wo.id)).entries)


class TestItDoesNotCryWolf:
    async def test_a_live_run_is_not_a_stall(self, admin_session: AsyncSession) -> None:
        for status in ("queued", "running", "waiting"):
            org, _wo, _svc = await _seed(admin_session, tasks=["A"], run_status=status)

            assert await _executor()._stalled_orders(admin_session, 10) == 0
            await admin_session.rollback()

    async def test_a_finished_checklist_is_not_a_stall(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=["A", "B"])
        for t in await svc.list_tasks(wo.id):
            t.status = "done"
        await svc.flush_tasks()
        await admin_session.commit()

        assert await _executor()._stalled_orders(admin_session, 10) == 0

    async def test_carried_work_does_not_count_as_outstanding(self, admin_session: AsyncSession) -> None:
        """'carried' means deliberately not doing it here — deciding to skip a step
        is not the same as abandoning it."""
        org, wo, svc = await _seed(admin_session, tasks=["A", "B"])
        tasks = await svc.list_tasks(wo.id)
        tasks[0].status = "done"
        tasks[1].status = "carried"
        await svc.flush_tasks()
        await admin_session.commit()

        assert await _executor()._stalled_orders(admin_session, 10) == 0

    async def test_an_order_nobody_dispatched_is_not_a_stall(self, admin_session: AsyncSession) -> None:
        """No run ever started: that is human work, or work waiting to be started."""
        org, wo, svc = await _seed(admin_session, tasks=["A"], run_status="")

        assert await _executor()._stalled_orders(admin_session, 10) == 0

    async def test_an_order_with_no_checklist_is_not_a_stall(self, admin_session: AsyncSession) -> None:
        # Nothing to be incomplete against; claiming a stall would be a guess.
        org, wo, svc = await _seed(admin_session, tasks=[])

        assert await _executor()._stalled_orders(admin_session, 10) == 0

    async def test_it_reports_once_not_every_tick(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=["A"])
        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert await _executor()._stalled_orders(admin_session, 10) == 0

    async def test_a_restarted_order_that_stalls_again_is_reported_again(self, admin_session: AsyncSession) -> None:
        """Throttled against the newest run, not a timestamp — so the second stall
        is as visible as the first."""
        org, wo, svc = await _seed(admin_session, tasks=["A"])
        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()
        admin_session.add(
            AgentRun(
                org_id=org.id,
                work_order_id=wo.id,
                provider="openai",
                model="m",
                trigger="work_order",
                status="done",
                input={},
            )
        )
        await admin_session.commit()

        assert await _executor()._stalled_orders(admin_session, 10) == 1


class TestTheNudgeBeforeStopping:
    """One reminder for an agent about to stop with its checklist unfinished.

    Deliberately not an instruction to keep working — the agent may have been right
    to stop, and being told to carry on regardless is how an agent invents work. It
    is asked to *account* for the gap, which either produces the work or produces a
    reason a person can read.
    """

    async def _run_for(self, admin_session: AsyncSession, wo, org) -> AgentRun:
        run = AgentRun(
            org_id=org.id,
            work_order_id=wo.id,
            provider="openai",
            model="m",
            trigger="work_order",
            status="running",
            input={},
        )
        admin_session.add(run)
        await admin_session.flush()
        return run

    async def test_it_names_what_is_left(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=["Crawl the site", "Write it up"])
        run = await self._run_for(admin_session, wo, org)

        nudge = await _executor()._unfinished_work_nudge(admin_session, org.id, run)

        assert nudge is not None
        assert "Crawl the site" in nudge and "T1" in nudge

    async def test_it_says_the_percentage_comes_from_the_tool(self, admin_session: AsyncSession) -> None:
        # The failure it exists for: an agent that did the work, said so in prose,
        # and never called update_work_order_task — so the order still read 17%.
        org, wo, svc = await _seed(admin_session, tasks=["A"])
        run = await self._run_for(admin_session, wo, org)

        nudge = await _executor()._unfinished_work_nudge(admin_session, org.id, run)

        assert "update_work_order_task" in nudge
        assert "not from your reply" in nudge

    async def test_a_finished_checklist_is_not_nudged(self, admin_session: AsyncSession) -> None:
        """A run that genuinely finished must not be asked to justify itself."""
        org, wo, svc = await _seed(admin_session, tasks=["A"])
        for t in await svc.list_tasks(wo.id):
            t.status = "done"
        await svc.flush_tasks()
        run = await self._run_for(admin_session, wo, org)

        assert await _executor()._unfinished_work_nudge(admin_session, org.id, run) is None

    async def test_an_order_with_no_checklist_is_not_nudged(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=[])
        run = await self._run_for(admin_session, wo, org)

        assert await _executor()._unfinished_work_nudge(admin_session, org.id, run) is None

    async def test_a_run_with_no_work_order_is_not_nudged(self, admin_session: AsyncSession) -> None:
        # Console chats and scheduled runs have no checklist to answer for.
        org, wo, svc = await _seed(admin_session, tasks=["A"])
        run = AgentRun(
            org_id=org.id,
            work_order_id=None,
            provider="openai",
            model="m",
            trigger="manual",
            status="running",
            input={},
        )
        admin_session.add(run)
        await admin_session.flush()

        assert await _executor()._unfinished_work_nudge(admin_session, org.id, run) is None
