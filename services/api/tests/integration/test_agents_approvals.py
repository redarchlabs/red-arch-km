"""Integration tests for the approvals inbox → run resume/deny round-trip."""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentRun
from api.models.org import Org
from api.services.agents.approvals import ApprovalService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_waiting_run(admin_session: AsyncSession) -> tuple[Org, AgentRun, AgentApproval]:
    org = Org(name=f"Appr-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="op", provider="openai", model="gpt-5-mini", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    run = AgentRun(
        agent_id=agent.id, provider="openai", model="gpt-5-mini", status="waiting",
        wait_kind="approval",
        input={"resume": {"messages": [{"role": "user", "content": "go"}],
                          "pending": [{"id": "c1", "name": "run_workflow", "arguments": {}}],
                          "approved": []}},
        org_id=org.id,
    )
    admin_session.add(run)
    await admin_session.flush()
    approval = AgentApproval(run_id=run.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
    admin_session.add(approval)
    await admin_session.commit()
    return org, run, approval


class TestApprovalResume:
    async def test_approve_requeues_run_with_tool_approved(self, admin_session: AsyncSession) -> None:
        org, run, approval = await _seed_waiting_run(admin_session)
        svc = ApprovalService(admin_session, org.id)
        updated = await svc.approve(approval.id, decided_by=None)
        await admin_session.commit()

        assert updated.status == "approved" and updated.decided_at is not None
        await admin_session.refresh(run)
        assert run.status == "queued"  # the worker sweep will resume it
        assert run.wait_kind is None
        assert "run_workflow" in run.input["resume"]["approved"]

    async def test_deny_fails_the_run(self, admin_session: AsyncSession) -> None:
        org, run, approval = await _seed_waiting_run(admin_session)
        svc = ApprovalService(admin_session, org.id)
        updated = await svc.deny(approval.id, decided_by=None)
        await admin_session.commit()

        assert updated.status == "denied"
        await admin_session.refresh(run)
        assert run.status == "error" and "denied" in (run.error or "")
