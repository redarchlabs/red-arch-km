"""One open reminder per waiting run — not a column of them.

A run parked on a question re-notifies once per escalation window so a stalled
approval is not silently forgotten. That part worked. What it also did was write a
*new* notification each window and leave every previous one open, so a run waiting
overnight grew a stack of identical "still waiting for you" rows, and answering the
question left all of them behind. An inbox that repeats itself is one people stop
reading, which defeats the reminder.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.config import get_settings
from api.models.agent import Agent
from api.models.agent_run import AgentNotification, AgentRun
from api.models.org import Org
from api.services.agents.run_executor import _REMINDER_TITLE, AgentRunExecutor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _executor() -> AgentRunExecutor:
    return AgentRunExecutor(get_settings())


async def _seed(admin_session: AsyncSession, *, status: str = "waiting", stale: bool = True) -> tuple[Org, AgentRun]:
    org = Org(name=f"Remind-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="analyst", provider="openai", model="m", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    settings = get_settings()
    age = timedelta(seconds=settings.agent_escalation_timeout_seconds + 60) if stale else timedelta(seconds=0)
    run = AgentRun(
        agent_id=agent.id,
        provider="openai",
        model="m",
        status=status,
        wait_kind="question",
        trigger="work_order",
        last_activity_at=datetime.now(UTC) - age,
        org_id=org.id,
    )
    admin_session.add(run)
    await admin_session.flush()
    return org, run


async def _reminders(session: AsyncSession, org_id: uuid.UUID) -> list[AgentNotification]:
    rows = (
        (
            await session.execute(
                select(AgentNotification).where(
                    AgentNotification.org_id == org_id, AgentNotification.title == _REMINDER_TITLE
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _open(session: AsyncSession, org_id: uuid.UUID) -> list[AgentNotification]:
    return [n for n in await _reminders(session, org_id) if n.status != "resolved"]


class TestOneReminderPerRun:
    async def test_a_stale_wait_is_reminded(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)

        assert await _executor()._backstop(admin_session, 10) == 1
        assert len(await _open(admin_session, org.id)) == 1

    async def test_a_fresh_wait_is_left_alone(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session, stale=False)

        assert await _executor()._backstop(admin_session, 10) == 0
        assert await _reminders(admin_session, org.id) == []

    async def test_the_next_window_supersedes_rather_than_stacks(self, admin_session: AsyncSession) -> None:
        # The bug: a run waiting overnight grew one identical row per window, all open.
        org, run = await _seed(admin_session)
        executor = _executor()

        for _ in range(3):
            await executor._backstop(admin_session, 10)
            run.last_activity_at = datetime.now(UTC) - timedelta(
                seconds=get_settings().agent_escalation_timeout_seconds + 60
            )
            await admin_session.flush()

        # Every window is still on the record…
        assert len(await _reminders(admin_session, org.id)) == 3
        # …but only the newest is asking for anything.
        assert len(await _open(admin_session, org.id)) == 1

    async def test_answering_retires_the_reminder(self, admin_session: AsyncSession) -> None:
        """Approving or answering is the person doing exactly what the reminder asked.
        Leaving it open then turns a nudge into a chore."""
        org, run = await _seed(admin_session)
        executor = _executor()
        await executor._backstop(admin_session, 10)
        assert len(await _open(admin_session, org.id)) == 1

        run.status = "queued"  # resumed by the answer
        await admin_session.flush()
        await executor._backstop(admin_session, 10)

        assert await _open(admin_session, org.id) == []

    async def test_a_finished_run_retires_it_too(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        executor = _executor()
        await executor._backstop(admin_session, 10)

        run.status = "cancelled"
        await admin_session.flush()
        await executor._backstop(admin_session, 10)

        assert await _open(admin_session, org.id) == []

    async def test_another_runs_reminder_is_untouched(self, admin_session: AsyncSession) -> None:
        """Superseding is per run. Clearing the org's whole reminder list because one
        run was answered would hide every other agent still waiting."""
        org, first = await _seed(admin_session)
        second = AgentRun(
            agent_id=first.agent_id,
            provider="openai",
            model="m",
            status="waiting",
            wait_kind="approval",
            trigger="work_order",
            last_activity_at=datetime.now(UTC)
            - timedelta(seconds=get_settings().agent_escalation_timeout_seconds + 60),
            org_id=org.id,
        )
        admin_session.add(second)
        await admin_session.flush()
        executor = _executor()
        await executor._backstop(admin_session, 10)
        assert len(await _open(admin_session, org.id)) == 2

        first.status = "queued"
        await admin_session.flush()
        await executor._backstop(admin_session, 10)

        still_open = await _open(admin_session, org.id)
        assert [n.run_id for n in still_open] == [second.id]

    async def test_a_workflow_run_is_not_reminded_here(self, admin_session: AsyncSession) -> None:
        # The step's timer boundary owns that SLA; a second stream splits the queue.
        org, run = await _seed(admin_session)
        run.trigger = "workflow"
        await admin_session.flush()

        assert await _executor()._backstop(admin_session, 10) == 0
