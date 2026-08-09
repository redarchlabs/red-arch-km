"""The interactive console must treat a parked run as a handoff, not a crash.

The console auto-approves ASK verdicts because the operator is present — so the
only thing that parks it is a *question* (``ask_human`` / ``consult_peer``), and a
question is answered on human time, which SSE cannot hold a request open for.

The failure this pins is quiet and expensive: ``RunParked`` is an ``Exception``, so
the console's generic error handler used to catch it, **roll the session back** —
discarding the question row and its notification, both written inside the handler —
and then finalize the run as ``error`` with the message "run parked: question". The
agent's question was destroyed, the run looked like it had crashed, and nothing in
the inbox said otherwise.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from api.config import get_settings
from api.models.agent import Agent
from api.models.agent_run import AgentQuestion, AgentRun
from api.models.org import Org
from api.repositories.agent_questions import AgentQuestionRepository
from api.services.agents import console as console_module
from api.services.agents import questions
from api.services.agents.console import AgentConsoleService
from api.services.agents.runtime import RunParked
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _no_wait_settings():
    """Settings with the inline answer window closed.

    The console now *waits* after parking, so that a person can answer in place.
    These tests are about what the park itself persists, and nobody is going to
    answer — so the window is set to zero and the run hands off immediately, which
    is the same path a real timeout takes.
    """
    return get_settings().model_copy(update={"agent_console_inline_wait_seconds": 0})


async def _seed(admin_session: AsyncSession) -> tuple[uuid.UUID, Agent]:
    org = Org(name=f"Console-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    agent = Agent(name="engineer", org_id=org.id, provider="openai", model="gpt-5-mini", kind="operator")
    admin_session.add(agent)
    await admin_session.commit()
    return org.id, agent


async def _drive(service: AgentConsoleService, agent_id: uuid.UUID) -> list[dict]:
    return [event async for event in service.run_stream(agent_id, [{"role": "user", "content": "go"}])]


class TestConsoleParking:
    @pytest.fixture(autouse=True)
    def _no_provider_key(self, monkeypatch):
        """The console refuses before it starts without a key; this run is about what
        happens *after* the loop begins, so stub the lookup rather than seed a secret."""

        async def _key(*_a, **_kw):
            return "test-key"

        monkeypatch.setattr(console_module, "resolve_provider_key", _key)

    async def test_a_question_leaves_the_run_waiting_not_errored(
        self, admin_session: AsyncSession, engine, monkeypatch
    ) -> None:
        org_id, agent = await _seed(admin_session)
        question_id = uuid.uuid4()

        async def _park(**kwargs):
            # Stands in for a handler calling ask_human: it writes the question on the
            # live session, then parks. The write must survive.
            session = kwargs["ctx"].session
            session.add(
                AgentQuestion(
                    id=question_id,
                    run_id=kwargs["ctx"].run_id,
                    tool_call_id="call_1",
                    asked_by_agent_id=agent.id,
                    audience="human",
                    question="Which region?",
                    status="pending",
                    org_id=org_id,
                )
            )
            await session.flush()
            parked = RunParked("question", {"question_id": str(question_id)})
            parked.messages = [{"role": "assistant", "content": ""}]
            parked.pending = [{"id": "call_1", "name": "ask_human", "arguments": {}}]
            raise parked

        monkeypatch.setattr(console_module, "run_agent_loop", _park)
        service = AgentConsoleService(
            org_id, _no_wait_settings(), async_sessionmaker(engine, expire_on_commit=False), actor_user_id=None
        )

        events = await _drive(service, agent.id)

        # The stream ends with a waiting frame, not an error.
        assert [e["type"] for e in events if e["type"] in ("waiting", "error")] == ["waiting"]
        waiting = next(e for e in events if e["type"] == "waiting")
        assert waiting["wait_kind"] == "question"
        assert waiting["question_id"] == str(question_id)

        run = (
            await admin_session.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(waiting["run_id"])))
        ).scalar_one()
        assert run.status == "waiting"
        assert run.wait_kind == "question"
        # Resume state is persisted, so the worker can continue the same turn once
        # the question is answered from the inbox.
        assert run.input["resume"]["pending"][0]["id"] == "call_1"

    async def test_the_question_survives_the_park(self, admin_session: AsyncSession, engine, monkeypatch) -> None:
        """The regression proper: the generic handler's rollback deleted this row, so
        the agent's question vanished and nothing ever reached the inbox."""
        org_id, agent = await _seed(admin_session)
        question_id = uuid.uuid4()

        async def _park(**kwargs):
            session = kwargs["ctx"].session
            session.add(
                AgentQuestion(
                    id=question_id,
                    run_id=kwargs["ctx"].run_id,
                    tool_call_id="call_1",
                    asked_by_agent_id=agent.id,
                    audience="human",
                    question="Which region?",
                    status="pending",
                    org_id=org_id,
                )
            )
            await session.flush()
            raise RunParked("question", {"question_id": str(question_id)})

        monkeypatch.setattr(console_module, "run_agent_loop", _park)
        service = AgentConsoleService(
            org_id, _no_wait_settings(), async_sessionmaker(engine, expire_on_commit=False), actor_user_id=None
        )

        await _drive(service, agent.id)

        row = (
            await admin_session.execute(select(AgentQuestion).where(AgentQuestion.id == question_id))
        ).scalar_one_or_none()
        assert row is not None
        assert row.status == "pending"
        assert row.question == "Which region?"


class TestInlineResume:
    """The feature: the stream stays open, the answer lands, the SAME run continues."""

    @pytest.fixture(autouse=True)
    def _no_provider_key(self, monkeypatch):
        async def _key(*_a, **_kw):
            return "test-key"

        monkeypatch.setattr(console_module, "resolve_provider_key", _key)

    async def test_an_answer_resumes_the_run_in_the_same_stream(
        self, admin_session: AsyncSession, engine, monkeypatch
    ) -> None:
        org_id, agent = await _seed(admin_session)
        calls: list[dict] = []

        async def _loop(**kwargs):
            """Park on the first pass; on the second, assert the answer arrived and
            finish. The second call is only reached if the console resumed in place
            rather than handing off."""
            calls.append(kwargs)
            if len(calls) == 1:
                session = kwargs["ctx"].session
                session.add(
                    AgentQuestion(
                        run_id=kwargs["ctx"].run_id,
                        tool_call_id="call_1",
                        asked_by_agent_id=agent.id,
                        audience="human",
                        question="Which region?",
                        status="pending",
                        org_id=org_id,
                    )
                )
                await session.flush()
                parked = RunParked("question", {})
                parked.messages = [{"role": "assistant", "content": ""}]
                parked.pending = [{"id": "call_1", "name": "ask_human", "arguments": {}}]
                raise parked
            return type(
                "R", (), {"final_content": "done", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            )()

        monkeypatch.setattr(console_module, "run_agent_loop", _loop)
        service = AgentConsoleService(
            org_id,
            get_settings().model_copy(update={"agent_console_inline_wait_seconds": 20}),
            async_sessionmaker(engine, expire_on_commit=False),
            actor_user_id=None,
        )

        async def _answer_when_waiting() -> None:
            """Stand in for the person typing an answer while the stream is open."""
            factory = async_sessionmaker(engine, expire_on_commit=False)
            for _ in range(60):
                await asyncio.sleep(0.25)
                async with factory() as s:
                    await set_tenant(s, str(org_id))
                    rows = await AgentQuestionRepository(s, org_id).list_pending(audience="human")
                    if not rows:
                        continue
                    await questions.record_answer(s, org_id, rows[0], answer="us-east-1")
                    await s.commit()
                    return

        answerer = asyncio.create_task(_answer_when_waiting())
        events = [e async for e in service.run_stream(agent.id, [{"role": "user", "content": "go"}])]
        await answerer

        assert len(calls) == 2, "the console did not resume the run in place"
        # The resumed pass was handed the answer, keyed to the call that blocked.
        assert calls[1]["resume_answers"]["call_1"]["answer"] == "us-east-1"
        assert [e["type"] for e in events if e["type"] in ("waiting", "handed_off", "error")] == ["waiting"]
        assert next(e for e in events if e["type"] == "waiting")["can_answer_inline"] is True

    async def test_nobody_answering_hands_off_without_losing_the_question(
        self, admin_session: AsyncSession, engine, monkeypatch
    ) -> None:
        """The timeout path must land on exactly the pre-inline behaviour."""
        org_id, agent = await _seed(admin_session)
        question_id = uuid.uuid4()

        async def _park(**kwargs):
            session = kwargs["ctx"].session
            session.add(
                AgentQuestion(
                    id=question_id,
                    run_id=kwargs["ctx"].run_id,
                    tool_call_id="call_1",
                    asked_by_agent_id=agent.id,
                    audience="human",
                    question="Which region?",
                    status="pending",
                    org_id=org_id,
                )
            )
            await session.flush()
            raise RunParked("question", {})

        monkeypatch.setattr(console_module, "run_agent_loop", _park)
        service = AgentConsoleService(
            org_id, _no_wait_settings(), async_sessionmaker(engine, expire_on_commit=False), actor_user_id=None
        )

        events = [e async for e in service.run_stream(agent.id, [{"role": "user", "content": "go"}])]

        assert [e["type"] for e in events if e["type"] in ("handed_off", "error")] == ["handed_off"]
        row = (await admin_session.execute(select(AgentQuestion).where(AgentQuestion.id == question_id))).scalar_one()
        assert row.status == "pending"  # still answerable from the inbox
        run = (await admin_session.execute(select(AgentRun).where(AgentRun.agent_id == agent.id))).scalars().first()
        assert run is not None and run.status == "waiting"
