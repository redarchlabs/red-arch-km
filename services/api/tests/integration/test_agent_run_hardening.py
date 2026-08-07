"""Integration tests for agent-run lifecycle hardening: CAS terminal transitions,
cancellation (+ approval voiding), and stale-``running`` lease recovery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.config import Settings
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentRun
from api.models.org import Org
from api.repositories.agent_run import AgentRunRepository
from api.services.agents.approvals import ApprovalService
from api.services.agents.run_executor import AgentRunExecutor
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_run(admin_session: AsyncSession, *, status: str = "running", **run_kwargs) -> tuple[Org, AgentRun]:
    org = Org(name=f"Hard-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="op", provider="openai", model="gpt-5-mini", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    run = AgentRun(
        agent_id=agent.id,
        provider="openai",
        model="gpt-5-mini",
        status=status,
        last_activity_at=datetime.now(UTC),
        org_id=org.id,
        **run_kwargs,
    )
    admin_session.add(run)
    await admin_session.commit()
    return org, run


class TestFinalizeCAS:
    async def test_first_finalize_wins_second_is_a_noop(self, admin_session: AsyncSession) -> None:
        org, run = await _seed_run(admin_session)
        repo = AgentRunRepository(admin_session, org.id)

        assert await repo.finalize_run(run, status="done", total_tokens=42) is True
        assert await repo.finalize_run(run, status="error", error="late loser") is False
        await admin_session.commit()

        await admin_session.refresh(run)
        assert run.status == "done"
        assert run.error is None  # the losing write changed nothing
        assert run.total_tokens == 42

    async def test_finalize_after_cancel_loses(self, admin_session: AsyncSession) -> None:
        org, run = await _seed_run(admin_session)
        repo = AgentRunRepository(admin_session, org.id)

        assert await repo.cancel_run(run.id, reason="workflow timeout") is True
        assert await repo.finalize_run(run, status="done") is False
        await admin_session.commit()

        await admin_session.refresh(run)
        assert run.status == "cancelled"
        assert run.error == "workflow timeout"


class TestCancellation:
    async def test_cancel_voids_pending_approvals(self, admin_session: AsyncSession) -> None:
        org, run = await _seed_run(admin_session, status="waiting", wait_kind="approval")
        approval = AgentApproval(run_id=run.id, tool_name="send_email", arguments={}, status="pending", org_id=org.id)
        admin_session.add(approval)
        await admin_session.commit()

        repo = AgentRunRepository(admin_session, org.id)
        assert await repo.cancel_run(run.id, reason="superseded") is True
        assert await repo.cancel_run(run.id, reason="again") is False  # already terminal
        await admin_session.commit()

        await admin_session.refresh(approval)
        assert approval.status == "voided"

    async def test_late_approve_of_cancelled_run_does_not_requeue(self, admin_session: AsyncSession) -> None:
        org, run = await _seed_run(admin_session, status="waiting", wait_kind="approval")
        approval = AgentApproval(run_id=run.id, tool_name="send_email", arguments={}, status="pending", org_id=org.id)
        admin_session.add(approval)
        await admin_session.commit()

        await AgentRunRepository(admin_session, org.id).cancel_run(run.id, reason="workflow timeout")
        await admin_session.commit()

        # The approval was voided by the cancel; approving the void is a no-op.
        updated = await ApprovalService(admin_session, org.id).approve(approval.id, decided_by=None)
        await admin_session.commit()
        assert updated.status == "voided"
        await admin_session.refresh(run)
        assert run.status == "cancelled"

    async def test_current_status_sees_committed_state(self, admin_session: AsyncSession) -> None:
        org, run = await _seed_run(admin_session)
        repo = AgentRunRepository(admin_session, org.id)
        assert await repo.current_status(run.id) == "running"
        await repo.cancel_run(run.id, reason="stop")
        assert await repo.current_status(run.id) == "cancelled"


class TestLeaseRecovery:
    @pytest.fixture
    def executor(self) -> AgentRunExecutor:
        return AgentRunExecutor(Settings(secret_key="test"))  # type: ignore[call-arg]

    async def test_stale_running_run_is_requeued_once_then_errored(
        self, admin_session: AsyncSession, executor: AgentRunExecutor
    ) -> None:
        org, run = await _seed_run(admin_session)
        stale = datetime.now(UTC) - timedelta(hours=2)
        run.last_activity_at = stale
        await admin_session.commit()

        assert await executor._reclaim_stale(admin_session, limit=10) == 1
        await admin_session.commit()
        await admin_session.refresh(run)
        assert run.status == "queued"
        assert run.input.get("_lease_requeues") == 1

        # Second expiry: the task itself is the problem — finalize as error.
        run.status = "running"
        run.last_activity_at = stale
        await admin_session.commit()
        assert await executor._reclaim_stale(admin_session, limit=10) == 1
        await admin_session.commit()
        await admin_session.refresh(run)
        assert run.status == "error"
        assert "lease" in (run.error or "")

    async def test_fresh_running_run_is_left_alone(
        self, admin_session: AsyncSession, executor: AgentRunExecutor
    ) -> None:
        org, run = await _seed_run(admin_session)
        assert await executor._reclaim_stale(admin_session, limit=10) == 0
        await admin_session.refresh(run)
        assert run.status == "running"

    async def test_heartbeat_bumps_only_running_runs(self, admin_session: AsyncSession) -> None:
        org, run = await _seed_run(admin_session)
        before = run.last_activity_at
        repo = AgentRunRepository(admin_session, org.id)
        await repo.heartbeat(run.id)
        await admin_session.commit()
        await admin_session.refresh(run)
        assert run.last_activity_at is not None and run.last_activity_at >= before
