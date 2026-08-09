"""A person can reply to a work order, and the reply reaches the agent.

Agents end runs with questions — sometimes because they were told to ask,
sometimes just conversationally ("Would you like me to do that?"). A finished
run has nothing listening, so that question reached nobody: the only way to
answer was to file a second work order, abandoning everything already in the
diary. This is the reply path that was missing.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.models.user import UserProfile
from api.services.agents.work_order_service import (
    WorkOrderService,
    WorkOrderValidationError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[Org, Agent, UserProfile]:
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Reply-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="chief", provider="openai", model="gpt-4.1-mini", kind="coordinator", org_id=org.id)
    profile = UserProfile(auth_subject=f"sub-{tag}", username=f"u-{tag}", email=f"u-{tag}@x.com")
    admin_session.add_all([agent, profile])
    await admin_session.commit()
    return org, agent, profile


async def _runs_for(session: AsyncSession, wo_id: uuid.UUID) -> list[AgentRun]:
    result = await session.execute(select(AgentRun).where(AgentRun.work_order_id == wo_id))
    return list(result.scalars().all())


async def _started(session: AsyncSession, org: Org, agent: Agent, profile: UserProfile):
    """An order under way whose run has finished — the state an agent leaves
    behind when it ends its turn with a question."""
    svc = WorkOrderService(session, org.id)
    wo = await svc.create_work_order(title="SEO Optimization", assigned_agent_id=agent.id)
    await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
    await session.commit()
    for run in await _runs_for(session, wo.id):
        run.status = "done"
    await session.commit()
    return svc, wo


class TestReplyingStartsTheAgentAgain:
    async def test_a_reply_queues_a_follow_up_run(self, admin_session: AsyncSession) -> None:
        org, agent, profile = await _seed(admin_session)
        svc, wo = await _started(admin_session, org, agent, profile)

        await svc.reply(wo.id, "Yes, go ahead and plan it.", actor_profile_id=profile.id)
        await admin_session.commit()

        queued = [r for r in await _runs_for(admin_session, wo.id) if r.status == "queued"]
        assert len(queued) == 1

    async def test_the_follow_up_carries_the_conversation(self, admin_session: AsyncSession) -> None:
        """A run given only the reply would restart the order from nothing and
        repeat work already in the diary."""
        org, agent, profile = await _seed(admin_session)
        svc, wo = await _started(admin_session, org, agent, profile)
        await svc.add_entry(wo.id, agent_id=agent.id, role=agent.name, text="Searched; found nothing on SEO.")
        await admin_session.commit()

        await svc.reply(wo.id, "Use the web research tool instead.", actor_profile_id=profile.id)
        await admin_session.commit()

        queued = [r for r in await _runs_for(admin_session, wo.id) if r.status == "queued"][0]
        task = queued.input["task"]
        assert "found nothing on SEO" in task
        assert "Use the web research tool instead" in task
        # Stated once, as the instruction — not also buried in the transcript.
        assert task.count("Use the web research tool instead") == 1

    async def test_the_reply_is_recorded_even_when_it_starts_nothing(self, admin_session: AsyncSession) -> None:
        org, _agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Unassigned")
        await admin_session.commit()

        await svc.reply(wo.id, "Someone please pick this up.", actor_profile_id=profile.id)
        await admin_session.commit()

        page = await svc.list_entries_page(wo.id)
        assert page.entries[-1].text == "Someone please pick this up."
        assert await _runs_for(admin_session, wo.id) == []

    async def test_replying_to_a_draft_does_not_start_it(self, admin_session: AsyncSession) -> None:
        """Adding to the record is not the decision to begin the work."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Not started", assigned_agent_id=agent.id)
        await admin_session.commit()

        await svc.reply(wo.id, "One more thing to consider.", actor_profile_id=profile.id)
        await admin_session.commit()

        assert await _runs_for(admin_session, wo.id) == []

    async def test_a_reply_during_a_live_run_says_it_was_not_delivered(self, admin_session: AsyncSession) -> None:
        """Delivering into a turn already in flight is a different problem. Letting
        the reply *look* delivered is the exact failure this method exists to fix."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Busy", assigned_agent_id=agent.id)
        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        await svc.reply(wo.id, "Actually, stop.", actor_profile_id=profile.id)
        await admin_session.commit()

        page = await svc.list_entries_page(wo.id)
        assert "not delivered" in page.entries[-1].text
        assert len([r for r in await _runs_for(admin_session, wo.id) if r.status == "queued"]) == 1

    async def test_an_empty_reply_is_refused(self, admin_session: AsyncSession) -> None:
        org, agent, profile = await _seed(admin_session)
        svc, wo = await _started(admin_session, org, agent, profile)

        with pytest.raises(WorkOrderValidationError):
            await svc.reply(wo.id, "   ", actor_profile_id=profile.id)
