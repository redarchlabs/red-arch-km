"""Saying something to a work order whose agents have already stopped.

The live panel's box steers a *run*. When every run on the order had finished, the
steer was refused — "Not delivered: that run is already done" — and the text was
dropped on the floor. That is exactly the moment a person has something to add: the
agents stopped because they were missing something, and the box on the page is the
obvious place to supply it. Three such messages were typed into a dead socket on one
order before anyone noticed they went nowhere.

A finished run cannot be resurrected (it would strand its finalize), so the message
becomes a *reply* on the work order instead: recorded in the diary, and a fresh run
started with that history as context.
"""

from __future__ import annotations

import uuid

import pytest
from api.config import get_settings
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.models.user import UserProfile
from api.routers.agent_live import _reply_instead
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession, *, status: str = "in_progress", assign: bool = True):
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Steer-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="chief-of-staff", provider="openai", model="m", kind="coordinator", org_id=org.id)
    profile = UserProfile(auth_subject=f"sub-{tag}", username=f"u-{tag}", email=f"u-{tag}@x.com")
    admin_session.add_all([agent, profile])
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(
        title="SEO Optimization",
        body="Check out SEO on redarchlabs.com",
        assigned_agent_id=agent.id if assign else None,
    )
    wo.status = status
    # The run that already ended — the one the browser is still aiming at.
    admin_session.add(
        AgentRun(
            org_id=org.id,
            agent_id=agent.id,
            work_order_id=wo.id,
            provider="openai",
            model="m",
            trigger="work_order",
            status="done",
            input={},
        )
    )
    await admin_session.flush()
    return org, wo, profile, svc


async def _runs(session: AsyncSession, wo_id) -> list[AgentRun]:
    rows = (await session.execute(select(AgentRun).where(AgentRun.work_order_id == wo_id))).scalars().all()
    return list(rows)


class TestItStartsTheWorkAgain:
    async def test_a_message_after_the_run_ends_starts_a_new_one(self, admin_session: AsyncSession) -> None:
        org, wo, profile, svc = await _seed(admin_session)

        frame = await _reply_instead(
            admin_session, get_settings(), org.id, profile.id, wo.id, "Use the web tool — you have it now.", None
        )

        assert frame["type"] == "steer_restarted"
        assert [r.status for r in await _runs(admin_session, wo.id)].count("queued") == 1

    async def test_what_the_person_said_is_on_the_record(self, admin_session: AsyncSession) -> None:
        org, wo, profile, svc = await _seed(admin_session)

        await _reply_instead(admin_session, get_settings(), org.id, profile.id, wo.id, "Crawl at 5 req/s.", None)

        entries = await svc.list_entries(wo.id)
        assert any("Crawl at 5 req/s." in e.text for e in entries)

    async def test_the_new_run_is_given_the_history(self, admin_session: AsyncSession) -> None:
        # A run handed only the reply would restart the order from nothing and redo
        # the work already in the diary.
        org, wo, profile, svc = await _seed(admin_session)

        await _reply_instead(admin_session, get_settings(), org.id, profile.id, wo.id, "You have web access now.", None)

        queued = [r for r in await _runs(admin_session, wo.id) if r.status == "queued"]
        task = queued[0].input["task"]
        assert "has just replied" in task
        assert "You have web access now." in task


class TestWhenItCannotStartAnything:
    async def test_a_finished_order_records_without_restarting(self, admin_session: AsyncSession) -> None:
        org, wo, profile, svc = await _seed(admin_session, status="done")

        frame = await _reply_instead(admin_session, get_settings(), org.id, profile.id, wo.id, "One more thing", None)

        assert frame["type"] == "steer_recorded"
        assert not [r for r in await _runs(admin_session, wo.id) if r.status == "queued"]
        # Recorded, not lost — the distinction the old rejection destroyed.
        assert any("One more thing" in e.text for e in await svc.list_entries(wo.id))

    async def test_an_unassigned_order_records_without_restarting(self, admin_session: AsyncSession) -> None:
        org, wo, profile, svc = await _seed(admin_session, assign=False)

        frame = await _reply_instead(admin_session, get_settings(), org.id, profile.id, wo.id, "Anyone?", None)

        assert frame["type"] == "steer_recorded"

    async def test_an_empty_message_is_still_refused(self, admin_session: AsyncSession) -> None:
        org, wo, profile, svc = await _seed(admin_session)

        frame = await _reply_instead(admin_session, get_settings(), org.id, profile.id, wo.id, "   ", None)

        assert frame["type"] == "steer_rejected"
