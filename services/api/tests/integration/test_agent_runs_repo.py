"""Integration tests for agent-run persistence: steps, finalize, RLS isolation."""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.repositories.agent_run import AgentRunRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[Org, Org, Agent]:
    org_a = Org(name=f"RunA-{uuid.uuid4().hex[:8]}", permission_number=1)
    org_b = Org(name=f"RunB-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add_all([org_a, org_b])
    await admin_session.flush()
    agent = Agent(name="runner", provider="openai", model="gpt-5-mini", org_id=org_a.id)
    admin_session.add(agent)
    await admin_session.commit()
    return org_a, org_b, agent


class TestAgentRunRepository:
    async def test_run_lifecycle_and_steps(self, admin_session: AsyncSession) -> None:
        org_a, _org_b, agent = await _seed(admin_session)
        repo = AgentRunRepository(admin_session, org_a.id)
        run = await repo.create_run(agent_id=agent.id, provider="openai", model="gpt-5-mini")
        assert run.status == "running" and run.started_at is not None

        s0 = await repo.add_step(run.id, kind="tool_call", name="run_workflow", content={"a": 1})
        s1 = await repo.add_step(run.id, kind="tool_result", name="run_workflow", content={"ok": True})
        assert (s0.seq, s1.seq) == (0, 1)

        steps = await repo.list_steps(run.id)
        assert [s.kind for s in steps] == ["tool_call", "tool_result"]

        await repo.finalize_run(run, status="done", prompt_tokens=10, completion_tokens=4, total_tokens=14)
        await admin_session.commit()
        reloaded = await repo.get_run(run.id)
        assert reloaded.status == "done" and reloaded.total_tokens == 14 and reloaded.finished_at is not None


class TestAgentRunRLS:
    async def test_rls_hides_other_orgs_runs(
        self, session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        org_a, org_b, agent = await _seed(admin_session)
        admin_session.add_all(
            [
                AgentRun(agent_id=agent.id, provider="openai", model="gpt-5-mini", org_id=org_a.id),
                AgentRun(provider="openai", model="gpt-5-mini", org_id=org_b.id),
            ]
        )
        await admin_session.commit()

        await set_tenant(session, str(org_a.id))
        visible = (await session.execute(select(AgentRun.org_id))).scalars().all()
        assert set(visible) == {org_a.id}

        await set_tenant(session, str(org_b.id))
        visible_b = (await session.execute(select(AgentRun.org_id))).scalars().all()
        assert set(visible_b) == {org_b.id}
