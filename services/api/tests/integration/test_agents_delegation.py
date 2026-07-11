"""Integration tests for step 5: work-order lifecycle + delegation enforcement."""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.services.agents.delegation import DelegationError, delegate
from api.services.agents.work_order_service import (
    WorkOrderService,
    WorkOrderValidationError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[Org, Agent, Agent, Agent]:
    org = Org(name=f"Del-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    boss = Agent(name="boss", provider="openai", model="gpt-5-mini", kind="coordinator", org_id=org.id)
    admin_session.add(boss)
    await admin_session.flush()
    report = Agent(name="report", provider="openai", model="gpt-5-mini", supervisor_id=boss.id, org_id=org.id)
    stranger = Agent(name="stranger", provider="openai", model="gpt-5-mini", org_id=org.id)
    admin_session.add_all([report, stranger])
    await admin_session.commit()
    return org, boss, report, stranger


class TestWorkOrderLifecycle:
    async def test_create_transition_and_progress(self, admin_session: AsyncSession) -> None:
        org, *_ = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Onboard Alice")
        assert wo.status == "draft" and wo.slug.startswith("onboard-alice-")

        await svc.set_status(wo.id, "in_progress")
        assert (await svc.get_work_order(wo.id)).status == "in_progress"

        with pytest.raises(WorkOrderValidationError):
            await svc.set_status(wo.id, "draft")  # no in_progress -> draft edge

        tasks = await svc.set_tasks(wo.id, [{"title": "A"}, {"title": "B"}])
        assert [t.key for t in tasks] == ["T1", "T2"]
        assert svc.progress(tasks) == 0.0
        tasks[0].status = "done"
        assert svc.progress(tasks) == 0.5


class TestDelegation:
    async def test_delegate_to_direct_report_queues_run(self, admin_session: AsyncSession) -> None:
        org, boss, report, _stranger = await _seed(admin_session)
        wo = await WorkOrderService(admin_session, org.id).create_work_order(title="Ship it")
        run = await delegate(
            admin_session, org.id, boss, "report", "Handle the thing",
            run_id=None, work_order_id=wo.id,
        )
        await admin_session.commit()
        assert run.agent_id == report.id
        assert run.status == "queued" and run.trigger == "delegation"
        assert run.work_order_id == wo.id
        # It really is persisted as a claimable queued run.
        queued = (await admin_session.execute(select(AgentRun).where(AgentRun.status == "queued"))).scalars().all()
        assert run.id in {r.id for r in queued}

    async def test_cannot_delegate_to_non_report(self, admin_session: AsyncSession) -> None:
        org, boss, _report, stranger = await _seed(admin_session)
        with pytest.raises(DelegationError):
            await delegate(admin_session, org.id, boss, "stranger", "nope", run_id=None, work_order_id=None)
