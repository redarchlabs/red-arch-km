"""Correctness around parking: who may answer, and what a park costs.

Three separate ways a parked run misreports or misbehaves, each invisible in
normal use:

* a human could settle a question addressed to an **agent**, silently discarding
  the consulted agent's entire run;
* a run that parks loses the tokens it spent before parking, so a run that asks
  questions bills for its last segment only;
* a console run never renewed its lease, so one lasting longer than the TTL was
  requeued and driven a *second* time, in parallel with itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.config import Settings
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.repositories.agent_run import AgentRunRepository
from api.services.agents import questions
from api.services.agents.run_executor import AgentRunExecutor
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[uuid.UUID, Agent, AgentRun]:
    org = Org(name=f"Park-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    agent = Agent(name="engineer", org_id=org.id, provider="openai", model="gpt-5-mini", kind="operator")
    admin_session.add(agent)
    await admin_session.flush()
    run = AgentRun(
        agent_id=agent.id,
        org_id=org.id,
        provider="openai",
        model="gpt-5-mini",
        status="waiting",
        wait_kind="question",
        input={"task": "x", "resume": {"messages": [], "pending": [], "approved": []}},
        last_activity_at=datetime.now(UTC),
    )
    admin_session.add(run)
    await admin_session.flush()
    return org.id, agent, run


class TestOnlyHumansSettleHumanQuestions:
    async def test_a_person_cannot_answer_a_peer_consult(self, admin_session: AsyncSession) -> None:
        """The inbox list filters on audience, but the fetch-by-id did not — so an
        id was enough to settle another agent's consult. The consulted agent would
        then find nothing waiting on it and throw away its whole run, while the
        asker resumed on an answer from someone who was never asked."""
        org_id, agent, run = await _seed(admin_session)
        row = await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="c1",
            asked_by_agent_id=agent.id,
            question="Is this safe?",
            audience="agent",
            target_agent_id=agent.id,
        )

        with pytest.raises(questions.QuestionNotFoundError):
            await questions.answer_question(admin_session, org_id, row.id, answer="sure")

        await admin_session.refresh(row)
        assert row.status == "pending"  # still the peer's to answer

    async def test_a_person_cannot_decline_a_peer_consult_either(self, admin_session: AsyncSession) -> None:
        org_id, agent, run = await _seed(admin_session)
        row = await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="c1",
            asked_by_agent_id=agent.id,
            question="Is this safe?",
            audience="agent",
        )

        with pytest.raises(questions.QuestionNotFoundError):
            await questions.decline_question(admin_session, org_id, row.id)

        await admin_session.refresh(row)
        assert row.status == "pending"

    async def test_a_human_question_is_still_answerable(self, admin_session: AsyncSession) -> None:
        org_id, agent, run = await _seed(admin_session)
        row = await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="c1",
            asked_by_agent_id=agent.id,
            question="Which region?",
        )

        outcome = await questions.answer_question(admin_session, org_id, row.id, answer="us-east-1")

        assert outcome.resumed is True
        assert outcome.question.status == "answered"


class TestParkedRunsKeepTheirTokens:
    async def test_parking_banks_the_tokens_spent_so_far(self, admin_session: AsyncSession) -> None:
        """The resumed drive counts from zero, so usage not banked at the park is
        gone. A run that asks two questions would bill only its final segment."""
        org_id, _agent, run = await _seed(admin_session)
        repo = AgentRunRepository(admin_session, org_id)

        await repo.mark_waiting(run, "question", prompt_tokens=100, completion_tokens=20, total_tokens=120)
        await admin_session.flush()

        await admin_session.refresh(run)
        assert (run.prompt_tokens, run.completion_tokens, run.total_tokens) == (100, 20, 120)

    async def test_each_park_adds_to_the_running_total(self, admin_session: AsyncSession) -> None:
        org_id, _agent, run = await _seed(admin_session)
        repo = AgentRunRepository(admin_session, org_id)

        await repo.mark_waiting(run, "question", prompt_tokens=100, completion_tokens=20, total_tokens=120)
        run.status = "waiting"
        await repo.mark_waiting(run, "question", prompt_tokens=50, completion_tokens=10, total_tokens=60)
        await admin_session.flush()

        await admin_session.refresh(run)
        assert run.total_tokens == 180

    async def test_finalize_adds_the_last_segment_rather_than_replacing(self, admin_session: AsyncSession) -> None:
        org_id, _agent, run = await _seed(admin_session)
        repo = AgentRunRepository(admin_session, org_id)
        await repo.mark_waiting(run, "question", prompt_tokens=100, completion_tokens=20, total_tokens=120)
        await admin_session.flush()

        assert await repo.finalize_run(run, status="done", prompt_tokens=7, completion_tokens=3, total_tokens=10)
        await admin_session.commit()

        await admin_session.refresh(run)
        assert run.total_tokens == 130  # 120 banked at the park + 10 after resuming
        assert run.prompt_tokens == 107

    async def test_a_run_that_never_parks_is_unchanged(self, admin_session: AsyncSession) -> None:
        """The common case must not drift: nothing banked, so the total is just what
        the single drive spent."""
        org_id, _agent, run = await _seed(admin_session)
        repo = AgentRunRepository(admin_session, org_id)

        assert await repo.finalize_run(run, status="done", prompt_tokens=9, completion_tokens=1, total_tokens=10)
        await admin_session.commit()

        await admin_session.refresh(run)
        assert run.total_tokens == 10


class TestConsoleLease:
    async def test_a_heartbeat_keeps_a_long_run_from_being_reclaimed(self, admin_session: AsyncSession) -> None:
        """A console run over the lease TTL was requeued and driven a second time in
        parallel with the live console, duplicating every tool side effect. The
        worker path always heartbeated; the console did not."""
        org_id, _agent, run = await _seed(admin_session)
        run.status = "running"
        run.last_activity_at = datetime.now(UTC) - timedelta(hours=2)
        await admin_session.commit()
        executor = AgentRunExecutor(Settings(secret_key="test"))  # type: ignore[call-arg]

        # Without a heartbeat the sweep reclaims it...
        assert await executor._reclaim_stale(admin_session, limit=10) == 1
        await admin_session.commit()

        # ...but a run whose lease is being renewed is left alone.
        run.status = "running"
        await AgentRunRepository(admin_session, org_id).heartbeat(run.id)
        await admin_session.commit()
        assert await executor._reclaim_stale(admin_session, limit=10) == 0
