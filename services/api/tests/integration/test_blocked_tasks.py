"""Blocking a step has to reach a person.

Seen live: an agent hit a capability it did not have, marked eight of nine steps
``blocked`` in a single turn, and finished. The checklist said so and nothing else
did — no alert, no badge, no email. The order's stall sweeper eventually noticed,
but only after the run ended, and its notification landed in a list with no badge
on it. Five hours passed before a person found out by opening the page.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentNotification
from api.models.org import Org
from api.services.agents.work_order_service import WorkOrderService, WorkOrderValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession, *, tasks: list[str]):
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Blocked-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="research-analyst", provider="openai", model="m", kind="advisory", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title="SEO check", assigned_agent_id=agent.id)
    wo.status = "in_progress"
    await svc.set_tasks(wo.id, [{"title": t, "sort_order": i} for i, t in enumerate(tasks)])
    await admin_session.flush()
    return org, wo, agent, svc


async def _alerts(session: AsyncSession, org_id) -> list[AgentNotification]:
    rows = (await session.execute(select(AgentNotification).where(AgentNotification.org_id == org_id))).scalars().all()
    return list(rows)


class TestItTellsSomeone:
    async def test_blocking_a_step_raises_an_escalation(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["Crawl", "Report"])

        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        alerts = await _alerts(admin_session, org.id)
        assert [a.kind for a in alerts] == ["escalation"]
        assert "is blocked" in alerts[0].title
        assert alerts[0].work_order_id == wo.id

    async def test_the_reason_is_in_the_diary(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["Crawl"])

        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        entries = await svc.list_entries(wo.id)
        assert any("⛔ Blocked: T1 — Crawl" in e.text for e in entries)

    async def test_progress_is_not_an_alert(self, admin_session: AsyncSession) -> None:
        # Every other transition is the agent working. Only blocking is it stopping.
        org, wo, agent, svc = await _seed(admin_session, tasks=["Crawl", "Report"])

        await svc.update_task_status(wo.id, "T1", "in_progress", agent=agent)
        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence="Crawled 42 pages, log in the diary.")

        assert await _alerts(admin_session, org.id) == []


class TestItDoesNotShout:
    async def test_a_wave_of_blocked_steps_is_one_alert(self, admin_session: AsyncSession) -> None:
        # The real case blocked eight steps in one turn. Eight notifications for one
        # cause is how a person learns to dismiss them unread.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B", "C", "D"])

        for key in ("T1", "T2", "T3", "T4"):
            await svc.update_task_status(wo.id, key, "blocked", agent=agent)

        alerts = await _alerts(admin_session, org.id)
        assert len(alerts) == 1
        # …but every one of them is on the record.
        entries = await svc.list_entries(wo.id)
        assert len([e for e in entries if e.text.startswith("⛔ Blocked:")]) == 4

    async def test_re_blocking_the_same_step_says_nothing_new(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"])

        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        assert len(await _alerts(admin_session, org.id)) == 1

    async def test_blocking_again_after_someone_clears_it_is_news(self, admin_session: AsyncSession) -> None:
        # Resolving is a person saying "seen it". An order that blocks again after
        # that is a new fact, and a diary-based throttle would have silenced it.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)
        for alert in await _alerts(admin_session, org.id):
            alert.status = "resolved"
        await admin_session.flush()

        await svc.update_task_status(wo.id, "T2", "blocked", agent=agent)

        assert len(await _alerts(admin_session, org.id)) == 2


class TestItTakesItBack:
    """An alert that outlives its cause spends the credibility of the next real one."""

    async def test_unblocking_the_last_step_retracts_the_alert(self, admin_session: AsyncSession) -> None:
        # Seen live: a step was blocked and marked done sixteen seconds later, and the
        # "needs a person before it can continue" alert was still there an hour on.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence="Got the answer from the filer.")

        assert [a.status for a in await _alerts(admin_session, org.id)] == ["resolved"]

    async def test_one_step_clearing_is_not_the_order_clearing(self, admin_session: AsyncSession) -> None:
        """Four blocked steps and one freed still needs the same person for the same
        reason — retracting there would hide a live problem."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)
        await svc.update_task_status(wo.id, "T2", "blocked", agent=agent)

        await svc.update_task_status(wo.id, "T1", "in_progress", agent=agent)

        assert [a.status for a in await _alerts(admin_session, org.id)] == ["unread"]

    async def test_re_planning_around_the_obstacle_retracts_it(self, admin_session: AsyncSession) -> None:
        # Re-planning is how an order most often stops being blocked. The steps the
        # alert named do not exist any more.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"])
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        await svc.set_tasks(wo.id, [{"title": "A different plan", "sort_order": 0}])

        assert [a.status for a in await _alerts(admin_session, org.id)] == ["resolved"]

    async def test_a_re_plan_that_is_still_blocked_keeps_the_alert(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"])
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        await svc.set_tasks(wo.id, [{"title": "Still stuck", "status": "blocked", "sort_order": 0}])

        assert [a.status for a in await _alerts(admin_session, org.id)] == ["unread"]

    async def test_blocking_after_a_retraction_alerts_again(self, admin_session: AsyncSession) -> None:
        """The retraction must not become a permanent silence — the order genuinely
        blocking again is news."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"])
        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)
        await svc.update_task_status(wo.id, "T1", "in_progress", agent=agent)

        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        assert sorted(a.status for a in await _alerts(admin_session, org.id)) == ["resolved", "unread"]


class TestTheEdges:
    async def test_an_unknown_step_names_the_real_keys(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])

        with pytest.raises(WorkOrderValidationError) as exc:
            await svc.update_task_status(wo.id, "T9", "blocked", agent=agent)

        assert "T1" in str(exc.value) and "T2" in str(exc.value)

    async def test_a_bad_status_is_refused(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"])

        with pytest.raises(WorkOrderValidationError):
            await svc.update_task_status(wo.id, "T1", "stuck", agent=agent)

    async def test_a_plan_that_arrives_blocked_still_alerts(self, admin_session: AsyncSession) -> None:
        # set_tasks replaces the whole list, so a step can be born blocked.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"])

        await svc.set_tasks(wo.id, [{"title": "A", "status": "blocked", "sort_order": 0}])

        assert len(await _alerts(admin_session, org.id)) == 1
