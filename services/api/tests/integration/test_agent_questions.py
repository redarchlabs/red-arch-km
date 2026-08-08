"""Integration tests for asking and answering questions (real PostgreSQL).

Before this, an agent could raise its hand but never hear back: ``consult_peer``
filed a notification and returned "sent", and there was no way at all to ask a
person something. The gap was not the asking — it was that nothing carried an
*answer* back into the run that was blocked on it.

So these tests are about the round trip, and about the ways it can fail to close.
A question that never resolves is worse than no question at all: the run sits in
``waiting`` holding its whole conversation in memory, and the only thing that ever
notices is an escalation backstop, hours later. Every path that ends a run
therefore has to settle the questions on both sides of it, and that is most of
what is asserted here.
"""

from __future__ import annotations

import uuid

import pytest
from api.config import get_settings
from api.models.agent import Agent
from api.models.agent_run import AgentQuestion, AgentRun
from api.models.org import Org
from api.repositories.agent_questions import AgentQuestionRepository
from api.services.agents import lifecycle, questions
from api.services.agents.delegation import ASK_HUMAN, CONSULT_PEER, REPLY_TO_PEER
from api.services.agents.runtime import RunFinished, RunParked
from api.services.agents.tools.spec import ToolContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org(admin_session: AsyncSession) -> uuid.UUID:
    org = Org(name=f"Q-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    return org.id


async def _agent(admin_session: AsyncSession, org_id: uuid.UUID, name: str, kind: str = "operator") -> Agent:
    agent = Agent(name=name, org_id=org_id, provider="openai", model="gpt-5-mini", kind=kind)
    admin_session.add(agent)
    await admin_session.flush()
    return agent


async def _run(admin_session: AsyncSession, org_id: uuid.UUID, agent: Agent, **kw) -> AgentRun:
    run = AgentRun(
        agent_id=agent.id,
        org_id=org_id,
        provider=agent.provider,
        model=agent.model,
        status=kw.pop("status", "running"),
        trigger=kw.pop("trigger", "manual"),
        input=kw.pop("input", {"task": "do the thing"}),
        **kw,
    )
    admin_session.add(run)
    await admin_session.flush()
    return run


def _ctx(session: AsyncSession, org_id: uuid.UUID, agent: Agent, run: AgentRun, call_id: str = "call_1") -> ToolContext:
    return ToolContext(
        session=session,
        org_id=org_id,
        settings=get_settings(),
        agent=agent,
        run_id=run.id,
        tool_call_id=call_id,
    )


async def _park(admin_session: AsyncSession, run: AgentRun, wait_kind: str, pending_call_id: str) -> None:
    """Persist the resume state the executor would write when a handler parks."""
    run.input = {
        **(run.input or {}),
        "resume": {
            "messages": [{"role": "assistant", "content": ""}],
            "pending": [{"id": pending_call_id, "name": "ask_human", "arguments": {}}],
            "approved": [],
        },
    }
    run.status = "waiting"
    run.wait_kind = wait_kind
    await admin_session.flush()


class TestAskHuman:
    async def test_asking_records_a_question_against_the_blocking_call(self, admin_session: AsyncSession) -> None:
        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)

        with pytest.raises(RunParked) as parked:
            await ASK_HUMAN.handler(
                _ctx(admin_session, org_id, agent, run, "call_7"),
                {"question": "Which region should this deploy to?", "context": "Shipping the API"},
            )

        assert parked.value.wait_kind == "question"
        rows = await AgentQuestionRepository(admin_session, org_id).list_pending()
        assert len(rows) == 1
        assert rows[0].tool_call_id == "call_7"
        assert rows[0].audience == "human"
        assert rows[0].context == "Shipping the API"
        assert rows[0].asked_by_agent_id == agent.id


class TestAnswering:
    async def test_an_answer_resumes_the_exact_call_that_blocked(self, admin_session: AsyncSession) -> None:
        """The answer has to come back as that call's *result*. Re-queuing the run
        without it would replay ask_human and ask the same question forever."""
        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        with pytest.raises(RunParked):
            await ASK_HUMAN.handler(_ctx(admin_session, org_id, agent, run, "call_7"), {"question": "Which region?"})
        await _park(admin_session, run, "question", "call_7")
        row = (await AgentQuestionRepository(admin_session, org_id).list_pending())[0]

        outcome = await questions.answer_question(admin_session, org_id, row.id, answer="us-east-1")

        assert outcome.resumed is True
        assert outcome.question.status == "answered"
        await admin_session.refresh(run)
        assert run.status == "queued"
        assert run.wait_kind is None
        assert run.input["resume"]["answers"]["call_7"]["answer"] == "us-east-1"

    async def test_declining_still_unblocks_the_run(self, admin_session: AsyncSession) -> None:
        """Refusing to answer must not mean refusing to respond — the run would
        otherwise sit in `waiting` until the escalation backstop noticed."""
        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        row = await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="call_7",
            asked_by_agent_id=agent.id,
            question="Which region?",
        )
        await _park(admin_session, run, "question", "call_7")

        outcome = await questions.decline_question(admin_session, org_id, row.id, reason="Not my call.")

        assert outcome.resumed is True
        assert outcome.question.status == "declined"
        await admin_session.refresh(run)
        assert run.status == "queued"
        answer = run.input["resume"]["answers"]["call_7"]
        assert answer["answered"] is False
        assert "judgement" in answer["guidance"]

    async def test_answering_a_run_that_already_ended_resumes_nothing(self, admin_session: AsyncSession) -> None:
        """A late answer must not restart a run whose owner already concluded it —
        the same hazard the approvals path voids for."""
        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent, status="cancelled")
        row = await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="call_7",
            asked_by_agent_id=agent.id,
            question="Which region?",
        )

        outcome = await questions.answer_question(admin_session, org_id, row.id, answer="us-east-1")

        assert outcome.resumed is False
        assert outcome.question.status == "voided"
        # The words are still on the record even though nobody acted on them.
        assert outcome.question.answer == "us-east-1"
        await admin_session.refresh(run)
        assert run.status == "cancelled"

    async def test_a_question_cannot_be_answered_twice(self, admin_session: AsyncSession) -> None:
        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        row = await questions.create_question(
            admin_session, org_id, run_id=run.id, tool_call_id="c", asked_by_agent_id=agent.id, question="?"
        )
        await _park(admin_session, run, "question", "c")
        await questions.answer_question(admin_session, org_id, row.id, answer="first")

        with pytest.raises(questions.QuestionError):
            await questions.answer_question(admin_session, org_id, row.id, answer="second")


class TestConsultRoundTrip:
    async def test_a_consult_queues_the_peer_and_parks_the_asker(self, admin_session: AsyncSession) -> None:
        org_id = await _org(admin_session)
        asker = await _agent(admin_session, org_id, "engineer")
        advisor = await _agent(admin_session, org_id, "security-analyst", kind="advisory")
        run = await _run(admin_session, org_id, asker)

        with pytest.raises(RunParked) as parked:
            await CONSULT_PEER.handler(
                _ctx(admin_session, org_id, asker, run, "call_3"),
                {"agent": "security-analyst", "question": "Is this token scope safe?"},
            )

        assert parked.value.wait_kind == "consult"
        peer_run = (await admin_session.execute(select(AgentRun).where(AgentRun.agent_id == advisor.id))).scalar_one()
        assert peer_run.status == "queued"
        assert peer_run.trigger == "consult"
        assert peer_run.parent_run_id == run.id
        assert "Is this token scope safe?" in peer_run.input["task"]
        row = (await AgentQuestionRepository(admin_session, org_id).list_pending())[0]
        assert row.peer_run_id == peer_run.id
        assert row.target_agent_id == advisor.id

    async def test_the_peers_answer_reaches_the_agent_that_asked(self, admin_session: AsyncSession) -> None:
        """The whole point. A consult that cannot return an answer is just a
        notification with extra steps."""
        org_id = await _org(admin_session)
        asker = await _agent(admin_session, org_id, "engineer")
        advisor = await _agent(admin_session, org_id, "security-analyst", kind="advisory")
        run = await _run(admin_session, org_id, asker)
        with pytest.raises(RunParked):
            await CONSULT_PEER.handler(
                _ctx(admin_session, org_id, asker, run, "call_3"),
                {"agent": "security-analyst", "question": "Is this safe?"},
            )
        await _park(admin_session, run, "consult", "call_3")
        peer_run = (await admin_session.execute(select(AgentRun).where(AgentRun.agent_id == advisor.id))).scalar_one()

        with pytest.raises(RunFinished) as finished:
            await REPLY_TO_PEER.handler(
                _ctx(admin_session, org_id, advisor, peer_run), {"answer": "No — the scope is too broad."}
            )

        assert finished.value.status == "done"
        await admin_session.refresh(run)
        assert run.status == "queued"
        answer = run.input["resume"]["answers"]["call_3"]
        assert answer["answer"] == "No — the scope is too broad."
        assert answer["answered_by"] == "security-analyst"

    async def test_reply_to_peer_refuses_when_no_one_asked(self, admin_session: AsyncSession) -> None:
        org_id = await _org(admin_session)
        advisor = await _agent(admin_session, org_id, "security-analyst", kind="advisory")
        run = await _run(admin_session, org_id, advisor)

        out = await REPLY_TO_PEER.handler(_ctx(admin_session, org_id, advisor, run), {"answer": "Sure."})

        assert "No one is waiting on you" in out["error"]


class TestUnansweredQuestionsCannotHangARun:
    async def test_a_peer_that_finishes_without_replying_still_unblocks_the_asker(
        self, admin_session: AsyncSession
    ) -> None:
        """The likeliest failure in practice: the advisor talks itself out, the run
        completes, and reply_to_peer was never called. Without the lifecycle hook
        the asker waits on a run that will never speak again."""
        org_id = await _org(admin_session)
        asker = await _agent(admin_session, org_id, "engineer")
        advisor = await _agent(admin_session, org_id, "security-analyst", kind="advisory")
        run = await _run(admin_session, org_id, asker)
        with pytest.raises(RunParked):
            await CONSULT_PEER.handler(
                _ctx(admin_session, org_id, asker, run, "call_3"),
                {"agent": "security-analyst", "question": "Is this safe?"},
            )
        await _park(admin_session, run, "consult", "call_3")
        peer_run = (await admin_session.execute(select(AgentRun).where(AgentRun.agent_id == advisor.id))).scalar_one()

        await lifecycle.finalize_run(admin_session, org_id, peer_run, status="done")

        await admin_session.refresh(run)
        assert run.status == "queued"
        assert run.input["resume"]["answers"]["call_3"]["answered"] is False
        row = (
            await admin_session.execute(select(AgentQuestion).where(AgentQuestion.peer_run_id == peer_run.id))
        ).scalar_one()
        assert row.status == "declined"

    async def test_an_asker_that_ends_voids_its_open_questions(self, admin_session: AsyncSession) -> None:
        """A pending question against a finished run is a trap: answering it later
        would try to re-queue a terminal run."""
        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        row = await questions.create_question(
            admin_session, org_id, run_id=run.id, tool_call_id="c", asked_by_agent_id=agent.id, question="?"
        )

        await lifecycle.finalize_run(admin_session, org_id, run, status="error", error="boom")

        await admin_session.refresh(row)
        assert row.status == "voided"

    async def test_an_abandoned_consult_run_is_cancelled(self, admin_session: AsyncSession) -> None:
        """The advisor's run costs real tokens. If nobody is left to receive the
        advice, stop paying for it."""
        org_id = await _org(admin_session)
        asker = await _agent(admin_session, org_id, "engineer")
        advisor = await _agent(admin_session, org_id, "security-analyst", kind="advisory")
        run = await _run(admin_session, org_id, asker)
        with pytest.raises(RunParked):
            await CONSULT_PEER.handler(
                _ctx(admin_session, org_id, asker, run, "call_3"),
                {"agent": "security-analyst", "question": "Is this safe?"},
            )
        peer_run = (await admin_session.execute(select(AgentRun).where(AgentRun.agent_id == advisor.id))).scalar_one()

        await lifecycle.cancel_run(admin_session, org_id, run.id, reason="operator stopped it")

        await admin_session.refresh(peer_run)
        assert peer_run.status == "cancelled"


class _Ctx:
    """Stands in for the OrgContext the route dependency injects."""

    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id
        self.user = type("U", (), {"profile_id": None})()


class TestInboxRoutes:
    async def test_the_inbox_shows_only_what_a_person_can_answer(self, admin_session: AsyncSession) -> None:
        """A peer consult already has an agent on the hook. Listing it for a human
        would invite them to answer someone else's question — and the consulted
        agent would keep running with nobody left listening."""
        from api.routers.agent_approvals import list_questions

        org_id = await _org(admin_session)
        asker = await _agent(admin_session, org_id, "engineer")
        advisor = await _agent(admin_session, org_id, "security-analyst", kind="advisory")
        run = await _run(admin_session, org_id, asker)
        await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="c1",
            asked_by_agent_id=asker.id,
            question="Which region?",
        )
        await questions.create_question(
            admin_session,
            org_id,
            run_id=run.id,
            tool_call_id="c2",
            asked_by_agent_id=asker.id,
            question="Is this safe?",
            audience="agent",
            target_agent_id=advisor.id,
        )

        listed = await list_questions(_Ctx(org_id), admin_session)

        assert [q.question for q in listed] == ["Which region?"]
        # Resolved so the inbox can say who is asking without a second round trip.
        assert listed[0].asked_by == "engineer"

    async def test_answering_reports_whether_an_agent_actually_resumed(self, admin_session: AsyncSession) -> None:
        from api.routers.agent_approvals import answer_question as answer_route
        from api.schemas.agent_run import AnswerRequest

        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        row = await questions.create_question(
            admin_session, org_id, run_id=run.id, tool_call_id="c", asked_by_agent_id=agent.id, question="?"
        )
        await _park(admin_session, run, "question", "c")

        result = await answer_route(row.id, AnswerRequest(answer="us-east-1"), _Ctx(org_id), admin_session)

        assert result.resumed is True
        assert result.question.status == "answered"

    async def test_answering_an_already_settled_question_is_a_conflict(self, admin_session: AsyncSession) -> None:
        from api.routers.agent_approvals import answer_question as answer_route
        from api.schemas.agent_run import AnswerRequest
        from fastapi import HTTPException

        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        row = await questions.create_question(
            admin_session, org_id, run_id=run.id, tool_call_id="c", asked_by_agent_id=agent.id, question="?"
        )
        await _park(admin_session, run, "question", "c")
        await answer_route(row.id, AnswerRequest(answer="first"), _Ctx(org_id), admin_session)

        with pytest.raises(HTTPException) as exc:
            await answer_route(row.id, AnswerRequest(answer="second"), _Ctx(org_id), admin_session)

        assert exc.value.status_code == 409

    async def test_another_orgs_question_is_not_reachable(self, admin_session: AsyncSession) -> None:
        from api.routers.agent_approvals import answer_question as answer_route
        from api.schemas.agent_run import AnswerRequest
        from fastapi import HTTPException

        org_id = await _org(admin_session)
        agent = await _agent(admin_session, org_id, "engineer")
        run = await _run(admin_session, org_id, agent)
        row = await questions.create_question(
            admin_session, org_id, run_id=run.id, tool_call_id="c", asked_by_agent_id=agent.id, question="?"
        )
        other_org_id = await _org(admin_session)

        with pytest.raises(HTTPException) as exc:
            await answer_route(row.id, AnswerRequest(answer="x"), _Ctx(other_org_id), admin_session)

        assert exc.value.status_code == 404
