"""The Agents page shows what each agent is doing right now.

The roster used to render exactly the same during a live run as it did overnight —
name, kind, provider, model, all of them true whether or not anything was happening.
The only way to learn an agent was mid-task, or had been sitting on a question for an
hour, was to open the work order it happened to be attached to.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentQuestion, AgentRun
from api.models.org import Org
from api.services.agents.roster_activity import roster_activity
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _org(admin_session: AsyncSession) -> Org:
    org = Org(name=f"Act-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    return org


async def _agent(admin_session: AsyncSession, org: Org, name: str) -> Agent:
    agent = Agent(name=name, provider="openai", model="m", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    return agent


async def _run(admin_session: AsyncSession, org: Org, agent: Agent, status: str) -> AgentRun:
    run = AgentRun(agent_id=agent.id, provider="openai", model="m", status=status, org_id=org.id)
    admin_session.add(run)
    await admin_session.flush()
    return run


class TestWorking:
    @pytest.mark.parametrize("status", ["queued", "running"])
    async def test_a_live_run_makes_an_agent_working(self, admin_session: AsyncSession, status: str) -> None:
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "backend-engineer")
        await _run(admin_session, org, agent, status)

        rows = await roster_activity(admin_session, org.id)

        assert [(r.agent_id, r.state) for r in rows] == [(agent.id, "working")]

    @pytest.mark.parametrize("status", ["done", "error", "cancelled"])
    async def test_a_finished_run_leaves_the_agent_idle(self, admin_session: AsyncSession, status: str) -> None:
        # Absent, not present-and-idle: the caller renders no badge for an agent it
        # does not hear about, so "nothing going on" costs nothing to say.
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "backend-engineer")
        await _run(admin_session, org, agent, status)

        assert await roster_activity(admin_session, org.id) == []

    async def test_concurrent_runs_are_counted(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "backend-engineer")
        await _run(admin_session, org, agent, "running")
        await _run(admin_session, org, agent, "queued")

        rows = await roster_activity(admin_session, org.id)

        assert rows[0].live_runs == 2


class TestNeedsYou:
    async def test_a_pending_approval_needs_you(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "backend-engineer")
        run = await _run(admin_session, org, agent, "waiting")
        admin_session.add(
            AgentApproval(run_id=run.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
        )
        await admin_session.flush()

        rows = await roster_activity(admin_session, org.id)

        assert [(r.state, r.waiting_on_you) for r in rows] == [("needs_you", 1)]

    async def test_a_pending_human_question_needs_you(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "research-analyst")
        run = await _run(admin_session, org, agent, "waiting")
        admin_session.add(
            AgentQuestion(
                run_id=run.id,
                tool_call_id="c1",
                audience="human",
                question="Which key should I use?",
                status="pending",
                org_id=org.id,
            )
        )
        await admin_session.flush()

        rows = await roster_activity(admin_session, org.id)

        assert [(r.state, r.waiting_on_you) for r in rows] == [("needs_you", 1)]

    async def test_a_consult_between_agents_is_not_your_problem(self, admin_session: AsyncSession) -> None:
        """A badge that cries for help when no help is wanted is a badge people stop
        reading. Nobody is asking a person anything here."""
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "research-analyst")
        run = await _run(admin_session, org, agent, "waiting")
        admin_session.add(
            AgentQuestion(
                run_id=run.id,
                tool_call_id="c1",
                audience="agent",
                question="What do you make of this?",
                status="pending",
                org_id=org.id,
            )
        )
        await admin_session.flush()

        assert await roster_activity(admin_session, org.id) == []

    async def test_an_answered_question_stops_needing_you(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "research-analyst")
        run = await _run(admin_session, org, agent, "waiting")
        admin_session.add(
            AgentQuestion(
                run_id=run.id,
                tool_call_id="c1",
                audience="human",
                question="Which key?",
                answer="that one",
                status="answered",
                org_id=org.id,
            )
        )
        await admin_session.flush()

        assert await roster_activity(admin_session, org.id) == []

    async def test_needing_you_outranks_working(self, admin_session: AsyncSession) -> None:
        """One run parked on a question while a second is underway. The question is
        the only part a person can do anything about, so it wins the badge."""
        org = await _org(admin_session)
        agent = await _agent(admin_session, org, "research-analyst")
        parked = await _run(admin_session, org, agent, "waiting")
        await _run(admin_session, org, agent, "running")
        admin_session.add(
            AgentQuestion(
                run_id=parked.id,
                tool_call_id="c1",
                audience="human",
                question="Which key?",
                status="pending",
                org_id=org.id,
            )
        )
        await admin_session.flush()

        rows = await roster_activity(admin_session, org.id)

        assert rows[0].state == "needs_you"
        assert rows[0].live_runs == 1


class TestScoping:
    async def test_another_orgs_agents_are_not_reported(self, admin_session: AsyncSession) -> None:
        mine = await _org(admin_session)
        theirs = await _org(admin_session)
        other = await _agent(admin_session, theirs, "their-engineer")
        await _run(admin_session, theirs, other, "running")

        assert await roster_activity(admin_session, mine.id) == []

    async def test_the_ones_that_need_you_are_listed_first(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        busy = await _agent(admin_session, org, "busy")
        stuck = await _agent(admin_session, org, "stuck")
        await _run(admin_session, org, busy, "running")
        parked = await _run(admin_session, org, stuck, "waiting")
        admin_session.add(
            AgentApproval(run_id=parked.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
        )
        await admin_session.flush()

        rows = await roster_activity(admin_session, org.id)

        assert [r.agent_id for r in rows] == [stuck.id, busy.id]
