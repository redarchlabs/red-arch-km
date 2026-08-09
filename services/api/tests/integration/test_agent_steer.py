"""Interjecting into a run that is already going.

The only channel to a running agent was answering a question it chose to ask. If
it set off in the wrong direction, the choice was to watch it finish or cancel it.

Delivery is a **pull**, not a push, because nothing outside the run can tell
whether it is safe to interrupt: ``status='running'`` covers streaming, gating and
mid-tool-batch identically, and a user turn injected where a tool result belongs is
rejected outright by both OpenAI and Anthropic — an error that finalizes the run.
So a steer is written to a table and the run drains it at the one seam where its
message list is well-formed.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.repositories.agent_run_messages import AgentRunMessageRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[Org, AgentRun]:
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Steer-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="chief", provider="openai", model="m", kind="coordinator", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    run = AgentRun(
        org_id=org.id,
        agent_id=agent.id,
        provider="openai",
        model="m",
        trigger="work_order",
        status="running",
        input={},
    )
    admin_session.add(run)
    await admin_session.commit()
    return org, run


class TestExactlyOnce:
    async def test_a_steer_is_delivered_once(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        repo = AgentRunMessageRepository(admin_session, org.id)
        await repo.add(run.id, "Use the web tool instead.")
        await admin_session.commit()

        first = await repo.drain(run.id)
        await admin_session.commit()
        second = await repo.drain(run.id)

        assert first == ["Use the web tool instead."]
        # Draining twice must not replay it: the model would act on it again.
        assert second == []

    async def test_several_steers_arrive_in_the_order_they_were_sent(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        repo = AgentRunMessageRepository(admin_session, org.id)
        for text in ("First thought", "Second thought", "Third thought"):
            await repo.add(run.id, text)
            await admin_session.commit()

        assert await repo.drain(run.id) == ["First thought", "Second thought", "Third thought"]

    async def test_one_run_never_receives_another_run_s_steer(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        _other_org, other_run = await _seed(admin_session)
        repo = AgentRunMessageRepository(admin_session, org.id)
        await repo.add(run.id, "For this run only.")
        await admin_session.commit()

        assert await repo.drain(other_run.id) == []
        assert await repo.drain(run.id) == ["For this run only."]

    async def test_nothing_queued_is_a_cheap_no_op(self, admin_session: AsyncSession) -> None:
        # This runs at the top of every turn of every run and almost always finds
        # nothing, so the empty case has to be the ordinary one.
        org, run = await _seed(admin_session)

        assert await AgentRunMessageRepository(admin_session, org.id).drain(run.id) == []
