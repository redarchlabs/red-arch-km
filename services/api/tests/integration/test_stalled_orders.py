"""An order whose agents have all stopped, with the work unfinished.

A run's ``done`` only ever meant the model stopped calling tools. Seen live: an
agent read the task list, asked three questions, wrote a paragraph and stopped,
leaving five of six tasks open — and the order sat ``in_progress`` at 17% looking
exactly like one being worked. Every other stall in this system surfaces; this one
was invisible, which made it the easiest to lose.

Detecting it was the first half. The second half is that in the common case there
is nothing for a person to decide: the agent closed a step, set the next one to
in_progress, wrote a tidy summary and stopped, with nothing blocking it. So the
sweep now picks the order back up — bounded, and only when a person is not the
thing it is stopped on. Where it cannot, it reports, exactly as before.
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


async def _runs(session: AsyncSession, wo_id, trigger: str) -> list[AgentRun]:
    rows = (
        (await session.execute(select(AgentRun).where(AgentRun.work_order_id == wo_id, AgentRun.trigger == trigger)))
        .scalars()
        .all()
    )
    return list(rows)


class TestItPicksTheOrderBackUp:
    async def test_a_stalled_order_is_continued(self, admin_session: AsyncSession) -> None:
        # The common case: nothing blocking, nobody asked anything, work left.
        org, wo, svc = await _seed(admin_session, tasks=["Crawl", "Report"])

        found = await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert found == 1
        assert len(await _runs(admin_session, wo.id, "continuation")) == 1
        # Continued, not escalated: there is nothing here for a person to decide.
        assert await _notices(admin_session, org.id) == []

    async def test_the_brief_carries_the_checklist_so_it_does_not_re_plan(self, admin_session: AsyncSession) -> None:
        """The observed failure of a restarted run: told only "carry on", a model
        rewrites the task list and the order loses the work already done."""
        org, wo, svc = await _seed(admin_session, tasks=["Crawl", "Report"])
        tasks = await svc.list_tasks(wo.id)
        tasks[0].status = "done"
        await svc.flush_tasks()
        await admin_session.commit()

        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        brief = (await _runs(admin_session, wo.id, "continuation"))[0].input["task"]
        assert "ALREADY DONE" in brief and "T1" in brief
        assert "STILL OPEN" in brief and "Report" in brief
        assert "do not re-plan" in brief

    async def test_the_pickup_is_in_the_diary(self, admin_session: AsyncSession) -> None:
        # The order's own record is where anyone reconstructing this will look.
        org, wo, svc = await _seed(admin_session, tasks=["A"])

        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert any("Continuing" in e.text for e in (await svc.list_entries_page(wo.id)).entries)


class TestWhenItWillNotContinue:
    async def test_a_pending_question_stops_it(self, admin_session: AsyncSession) -> None:
        """The agent stopped because it asked. Restarting it talks over the answer."""
        from api.models.agent_run import AgentQuestion

        org, wo, svc = await _seed(admin_session, tasks=["A"])
        run = (await _runs(admin_session, wo.id, "work_order"))[0]
        admin_session.add(
            AgentQuestion(
                run_id=run.id,
                tool_call_id="c1",
                audience="human",
                question="which one?",
                status="pending",
                org_id=org.id,
            )
        )
        await admin_session.commit()

        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert await _runs(admin_session, wo.id, "continuation") == []
        assert any("stopped with work outstanding" in n.title for n in await _notices(admin_session, org.id))

    async def test_an_all_blocked_order_stops_it(self, admin_session: AsyncSession) -> None:
        # Blocked means it needs someone else; the same agent will not change that.
        org, wo, svc = await _seed(admin_session, tasks=["A", "B"])
        for t in await svc.list_tasks(wo.id):
            t.status = "blocked"
        await svc.flush_tasks()
        await admin_session.commit()

        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert await _runs(admin_session, wo.id, "continuation") == []
        assert any("stopped with work outstanding" in n.title for n in await _notices(admin_session, org.id))

    async def test_a_pickup_that_settled_nothing_is_not_repeated(self, admin_session: AsyncSession) -> None:
        """A continuation that closed no step will not close one next time either,
        and it bills for every turn. A person is better placed to say why."""
        org, wo, svc = await _seed(admin_session, tasks=["A", "B"])
        executor = _executor()
        await executor._stalled_orders(admin_session, 10)
        await admin_session.commit()
        for run in await _runs(admin_session, wo.id, "continuation"):
            run.status = "done"
        await admin_session.commit()

        await executor._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert len(await _runs(admin_session, wo.id, "continuation")) == 1
        assert any("stopped with work outstanding" in n.title for n in await _notices(admin_session, org.id))

    async def test_progress_earns_another_pickup(self, admin_session: AsyncSession) -> None:
        org, wo, svc = await _seed(admin_session, tasks=["A", "B", "C"])
        executor = _executor()
        await executor._stalled_orders(admin_session, 10)
        await admin_session.commit()
        for run in await _runs(admin_session, wo.id, "continuation"):
            run.status = "done"
        tasks = await svc.list_tasks(wo.id)
        tasks[0].status = "done"  # the pickup actually did something
        await svc.flush_tasks()
        await admin_session.commit()

        await executor._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert len(await _runs(admin_session, wo.id, "continuation")) == 2

    async def test_it_gives_up_after_the_cap(self, admin_session: AsyncSession) -> None:
        from api.services.agents.work_order_service import MAX_CONTINUATIONS

        org, wo, svc = await _seed(admin_session, tasks=[f"T{i}" for i in range(MAX_CONTINUATIONS + 3)])
        executor = _executor()
        tasks = await svc.list_tasks(wo.id)
        for i in range(MAX_CONTINUATIONS + 1):
            await executor._stalled_orders(admin_session, 10)
            for run in await _runs(admin_session, wo.id, "continuation"):
                run.status = "done"
            tasks[i].status = "done"  # progress every round, so only the cap can stop it
            await svc.flush_tasks()
            await admin_session.commit()

        assert len(await _runs(admin_session, wo.id, "continuation")) == MAX_CONTINUATIONS
        assert any("stopped with work outstanding" in n.title for n in await _notices(admin_session, org.id))


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
        wo.assigned_agent_id = None  # nothing to continue, so it falls through to reporting
        await admin_session.commit()
        await _executor()._stalled_orders(admin_session, 10)
        await admin_session.commit()

        assert await _executor()._stalled_orders(admin_session, 10) == 0

    async def test_a_restarted_order_that_stalls_again_is_reported_again(self, admin_session: AsyncSession) -> None:
        """Throttled against the newest run, not a timestamp — so the second stall
        is as visible as the first."""
        org, wo, svc = await _seed(admin_session, tasks=["A"])
        wo.assigned_agent_id = None  # unassignable, so it reports rather than continuing
        await admin_session.commit()
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
