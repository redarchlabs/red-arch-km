"""Exactly one party may drive a run (real PostgreSQL).

Answering a question sets the run back to ``queued``, which makes it eligible for
the background sweep. If a console is *also* waiting to resume that run inline,
both can want it — and if both proceed they replay the same pending tool batch:
duplicated side effects, two billed LLM turns, interleaved transcript rows.

None of that fails loudly. ``agent_run_steps.seq`` is ``max+1`` with no unique
constraint and there is no ``version_id_col``, so concurrent writers interleave
and the last write wins rather than raising. The claim CAS is the only thing
standing between those two executors, which is why it gets this much cover.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from api.config import Settings
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.repositories.agent_run import AgentRunRepository
from api.services.agents.run_executor import AgentRunExecutor
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _queued_run(admin_session: AsyncSession) -> tuple[uuid.UUID, AgentRun]:
    org = Org(name=f"Claim-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    agent = Agent(name="engineer", org_id=org.id, provider="openai", model="gpt-5-mini", kind="operator")
    admin_session.add(agent)
    await admin_session.flush()
    run = AgentRun(
        agent_id=agent.id,
        org_id=org.id,
        provider="openai",
        model="gpt-5-mini",
        status="queued",
        input={"task": "x", "resume": {"messages": [], "pending": [], "approved": []}},
        last_activity_at=datetime.now(UTC),
    )
    admin_session.add(run)
    await admin_session.commit()
    return org.id, run


class TestClaimIsExclusive:
    async def test_the_first_claim_wins_and_the_second_gets_nothing(self, admin_session: AsyncSession) -> None:
        org_id, run = await _queued_run(admin_session)
        repo = AgentRunRepository(admin_session, org_id)

        assert await repo.claim_run(run.id) is not None
        assert await repo.claim_run(run.id) is None  # already running — stand down

        await admin_session.refresh(run)
        assert run.status == "running"

    async def test_a_console_claim_hides_the_run_from_the_sweep(self, admin_session: AsyncSession) -> None:
        """The console got there first, so the sweep must find nothing to claim."""
        org_id, run = await _queued_run(admin_session)

        claimed = await AgentRunRepository(admin_session, org_id).claim_run(run.id)
        await admin_session.commit()
        assert claimed is not None

        executor = AgentRunExecutor(Settings(secret_key="test"))  # type: ignore[call-arg]
        swept = await executor._claim(admin_session, limit=10)

        assert run.id not in [rid for rid, _ in swept]

    async def test_a_swept_run_cannot_then_be_claimed_by_a_console(self, admin_session: AsyncSession) -> None:
        """And the other way round: the sweep won, so the console stands down and
        the run continues in the background — today's behaviour, not a bug."""
        org_id, run = await _queued_run(admin_session)
        executor = AgentRunExecutor(Settings(secret_key="test"))  # type: ignore[call-arg]

        swept = await executor._claim(admin_session, limit=10)
        await admin_session.commit()
        assert run.id in [rid for rid, _ in swept]

        assert await AgentRunRepository(admin_session, org_id).claim_run(run.id) is None

    async def test_a_terminal_run_is_never_claimable(self, admin_session: AsyncSession) -> None:
        """A late answer must not resurrect a run whose owner already concluded it."""
        org_id, run = await _queued_run(admin_session)
        run.status = "cancelled"
        await admin_session.commit()

        assert await AgentRunRepository(admin_session, org_id).claim_run(run.id) is None

    async def test_a_waiting_run_is_not_claimable_until_it_is_answered(self, admin_session: AsyncSession) -> None:
        """`waiting` means the question is still open. Claiming then would resume a
        turn whose answer has not been written."""
        org_id, run = await _queued_run(admin_session)
        run.status = "waiting"
        run.wait_kind = "question"
        await admin_session.commit()

        assert await AgentRunRepository(admin_session, org_id).claim_run(run.id) is None

    async def test_another_orgs_run_is_not_claimable(self, admin_session: AsyncSession) -> None:
        org_id, run = await _queued_run(admin_session)
        other_org_id, _ = await _queued_run(admin_session)
        await set_tenant(admin_session, str(other_org_id))

        assert await AgentRunRepository(admin_session, other_org_id).claim_run(run.id) is None
