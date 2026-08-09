"""A plan is read by a board before a person is asked to approve it.

Roughly a tenth of what an agent produces is confident and wrong, and it looks
exactly like the nine tenths that are fine. A reviewer with a different lens often
catches it; the author never can. So the board sits between the plan and the human,
and what a person is finally asked to approve arrives with its objections attached.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentRun
from api.models.org import Org
from api.models.work_order import WorkOrderEntry
from api.services.agents import review_board as rb
from api.services.agents.runtime import RunParked
from api.services.agents.tools.plan_mode import SUBMIT_PLAN
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

BOARDS = {
    "engineering": [
        {"agent": "devils-advocate", "lens": "Argue why this is wrong."},
        {"agent": "security-analyst", "lens": "Threat model."},
    ]
}


@dataclass
class _Ctx:
    session: Any
    org_id: uuid.UUID
    work_order_id: uuid.UUID | None
    agent: Any = None
    run_id: uuid.UUID | None = None
    settings: Any = None
    actor_user_id: uuid.UUID | None = None
    tool_call_id: str | None = "call_1"
    _extra: dict = field(default_factory=dict)


async def _seed(admin_session: AsyncSession, *, level: str = "standard", boards: Any = None):
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Board-{tag}", permission_number=1, review_boards=BOARDS if boards is None else boards)
    admin_session.add(org)
    await admin_session.flush()
    author = Agent(name="solution-architect", provider="openai", model="big", kind="advisory", org_id=org.id)
    reviewers = [
        Agent(name=n, provider="openai", model="big", review_model="mini", kind=k, org_id=org.id)
        for n, k in (("devils-advocate", "advisory"), ("security-analyst", "advisory"))
    ]
    admin_session.add_all([author, *reviewers])
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title="Rebuild search", assigned_agent_id=author.id, mode="plan")
    wo.review_level = level
    wo.status = "in_progress"
    run = AgentRun(
        org_id=org.id,
        agent_id=author.id,
        work_order_id=wo.id,
        provider="openai",
        model="big",
        trigger="work_order",
        status="running",
        input={},
    )
    admin_session.add(run)
    await admin_session.commit()
    return org, author, wo, run, svc


async def _entries(session: AsyncSession, wo_id) -> list[WorkOrderEntry]:
    rows = (
        (
            await session.execute(
                select(WorkOrderEntry).where(WorkOrderEntry.work_order_id == wo_id).order_by(WorkOrderEntry.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _reviewer_runs(session: AsyncSession, wo_id) -> list[AgentRun]:
    rows = (
        (await session.execute(select(AgentRun).where(AgentRun.work_order_id == wo_id, AgentRun.trigger == "consult")))
        .scalars()
        .all()
    )
    return list(rows)


class TestConvening:
    async def test_submitting_a_plan_convenes_the_board_first(self, admin_session: AsyncSession) -> None:
        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)

        with pytest.raises(RunParked) as parked:
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        await admin_session.commit()

        # Parked on the board, not on the human — the ordering is the point.
        assert parked.value.wait_kind == "consult"
        assert sorted(parked.value.payload["board"]) == ["devils-advocate", "security-analyst"]

    async def test_the_whole_board_is_asked_on_one_tool_call(self, admin_session: AsyncSession) -> None:
        """So the author parks once and wakes when the LAST verdict lands. Serial
        consults would cost the same tokens and several times the wall clock."""
        from api.models.agent_run import AgentQuestion

        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)

        with pytest.raises(RunParked):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        await admin_session.commit()

        questions = (
            (await admin_session.execute(select(AgentQuestion).where(AgentQuestion.run_id == run.id))).scalars().all()
        )
        assert len(questions) == 2
        assert {q.tool_call_id for q in questions} == {"call_1"}

    async def test_reviewers_run_on_the_cheap_model(self, admin_session: AsyncSession) -> None:
        # Reading a plan costs a fraction of writing one, so a board need not sit on
        # the author's model.
        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)

        with pytest.raises(RunParked):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        await admin_session.commit()

        assert {r.model for r in await _reviewer_runs(admin_session, wo.id)} == {"mini"}

    async def test_the_author_is_never_seated_on_its_own_board(self, admin_session: AsyncSession) -> None:
        """Writer is not reviewer — the property that makes a review mean anything."""
        boards = {
            "engineering": [{"agent": "solution-architect", "lens": "x"}, {"agent": "devils-advocate", "lens": "y"}]
        }
        org, author, wo, run, _svc = await _seed(admin_session, boards=boards)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)

        with pytest.raises(RunParked) as parked:
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})

        assert "solution-architect" not in parked.value.payload["board"]

    async def test_review_level_none_skips_straight_to_the_human(self, admin_session: AsyncSession) -> None:
        org, author, wo, run, _svc = await _seed(admin_session, level="none")
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)

        with pytest.raises(RunParked) as parked:
            await SUBMIT_PLAN.handler(ctx, {"summary": "Small change."})

        assert parked.value.wait_kind == "approval"


class TestTheVerdict:
    async def _convene_then(self, admin_session, verdicts: dict[str, str]):
        org, author, wo, run, svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)
        with pytest.raises(RunParked):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        for reviewer, verdict in verdicts.items():
            admin_session.add(
                WorkOrderEntry(
                    work_order_id=wo.id,
                    org_id=org.id,
                    role="review",
                    text=rb.verdict_marker(reviewer, verdict, "because"),
                )
            )
        await admin_session.commit()
        return org, author, wo, run, svc, ctx

    async def test_a_fail_comes_back_to_the_author_not_to_the_human(self, admin_session: AsyncSession) -> None:
        org, author, wo, run, svc, ctx = await self._convene_then(
            admin_session, {"devils-advocate": rb.PASS, "security-analyst": rb.FAIL}
        )

        out = await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})

        assert out["review"] == "changes requested"
        assert out["failed"] == ["security-analyst"]
        # Nothing has been asked of a person yet.
        assert (await svc.get_work_order(wo.id)).mode == "plan"

    async def test_all_pass_moves_on_to_the_human(self, admin_session: AsyncSession) -> None:
        org, author, wo, run, svc, ctx = await self._convene_then(
            admin_session, {"devils-advocate": rb.PASS, "security-analyst": rb.PASS}
        )

        with pytest.raises(RunParked) as parked:
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        await admin_session.commit()

        assert parked.value.wait_kind == "approval"
        # And the pass is on the record, so a second call does not re-convene.
        assert any(rb.PASSED in e.text for e in await _entries(admin_session, wo.id))

    async def test_a_passed_board_is_not_reconvened(self, admin_session: AsyncSession) -> None:
        """Re-reviewing text nobody changed is a wasted round every retry."""
        org, author, wo, run, svc, ctx = await self._convene_then(
            admin_session, {"devils-advocate": rb.PASS, "security-analyst": rb.PASS}
        )
        with pytest.raises(RunParked):
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        await admin_session.commit()
        before = len(await _reviewer_runs(admin_session, wo.id))

        admin_session.add(
            AgentApproval(run_id=run.id, org_id=org.id, tool_name="submit_plan", arguments={}, status="approved")
        )
        await admin_session.flush()
        with pytest.raises(Exception):  # RunFinished — the plan is accepted
            await SUBMIT_PLAN.handler(ctx, {"summary": "Replace the index."})
        await admin_session.commit()

        assert len(await _reviewer_runs(admin_session, wo.id)) == before


class TestTheDeliveryGate:
    """A plan that passed review can still produce a wrong result, and that is
    where confident-wrong output usually surfaces. So the finished deliverable
    goes past the board too, before it reaches a person."""

    async def test_reporting_finished_work_convenes_the_board(self, admin_session: AsyncSession) -> None:
        from api.services.agents.delegation import REQUEST_REVIEW

        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)

        with pytest.raises(RunParked) as parked:
            await REQUEST_REVIEW.handler(ctx, {"summary": "Search is rebuilt and indexed."})
        await admin_session.commit()

        assert parked.value.payload["review"] == "delivery"

    async def test_a_fail_goes_back_to_the_author(self, admin_session: AsyncSession) -> None:
        from api.services.agents.delegation import REQUEST_REVIEW

        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)
        with pytest.raises(RunParked):
            await REQUEST_REVIEW.handler(ctx, {"summary": "Search is rebuilt and indexed."})
        for reviewer, verdict in (("devils-advocate", rb.PASS), ("security-analyst", rb.FAIL)):
            admin_session.add(
                WorkOrderEntry(
                    work_order_id=wo.id,
                    org_id=org.id,
                    role="review",
                    text=rb.verdict_marker(reviewer, verdict, "the index is not covered by tests"),
                )
            )
        await admin_session.commit()

        out = await REQUEST_REVIEW.handler(ctx, {"summary": "Search is rebuilt and indexed."})

        assert out["review"] == "changes requested"
        assert out["failed"] == ["security-analyst"]

    async def test_the_two_gates_are_independent(self, admin_session: AsyncSession) -> None:
        """A passed plan says nothing about the delivery, and vice versa."""
        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo.id, agent=author, run_id=run.id)
        admin_session.add(
            WorkOrderEntry(
                work_order_id=wo.id,
                org_id=org.id,
                role="review",
                text=f"🏛️ {rb.PASSED} plan ({rb.fingerprint('anything')}) — all",
            )
        )
        await admin_session.commit()

        from api.services.agents.delegation import REQUEST_REVIEW

        with pytest.raises(RunParked):
            await REQUEST_REVIEW.handler(ctx, {"summary": "Delivered."})

    async def test_a_run_with_no_work_order_is_untouched(self, admin_session: AsyncSession) -> None:
        # Console chats and scheduled runs report without a board; the gate must be
        # invisible to every path it was not meant to cover.
        from api.services.agents.delegation import REQUEST_REVIEW

        org, author, wo, run, _svc = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=None, agent=author, run_id=run.id)

        out = await REQUEST_REVIEW.handler(ctx, {"summary": "Done."})

        assert out["status"] == "pending"
