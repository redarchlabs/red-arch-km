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

import uuid

import pytest
from api.config import get_settings
from api.models.agent import Agent
from api.models.agent_run import AgentQuestion, AgentRun
from api.models.org import Org
from api.services.agents import console as console_module
from api.services.agents.console import AgentConsoleService
from api.services.agents.runtime import RunParked
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


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
            org_id, get_settings(), async_sessionmaker(engine, expire_on_commit=False), actor_user_id=None
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
            org_id, get_settings(), async_sessionmaker(engine, expire_on_commit=False), actor_user_id=None
        )

        await _drive(service, agent.id)

        row = (
            await admin_session.execute(select(AgentQuestion).where(AgentQuestion.id == question_id))
        ).scalar_one_or_none()
        assert row is not None
        assert row.status == "pending"
        assert row.question == "Which region?"
