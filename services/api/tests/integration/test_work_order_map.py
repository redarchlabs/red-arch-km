"""The interaction map: one lane per participant, with what each did placed in time.

The diary is a flat list of sentences, which loses the shape of the work. A work
order that fans out to a consult and a delegation reads as unrelated lines with no
indication that one agent is blocked on another, which branch stalled, or how long
anything took.

A tree of runs would answer "who invoked whom" — the least interesting question
once more than two agents are involved, because it says nothing about *when* and
nothing about who is idle versus blocked. Lanes over a shared clock put parallel
branches side by side and make a gap visibly a gap.

Every event is derived from a structured row — runs, questions, approvals — and
never from parsing the diary's prose, which is written for people and reworded
freely.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentQuestion, AgentRun
from api.models.org import Org
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

T0 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


async def _seed(admin_session: AsyncSession) -> tuple[Org, Agent, Agent, Agent]:
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"Map-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    boss = Agent(name="chief", provider="openai", model="gpt-5-mini", kind="coordinator", org_id=org.id, avatar="🧭")
    admin_session.add(boss)
    await admin_session.flush()
    advisor = Agent(name="devils-advocate", provider="openai", model="gpt-5-mini", kind="advisory", org_id=org.id)
    worker = Agent(name="research-analyst", provider="openai", model="gpt-5-mini", org_id=org.id, supervisor_id=boss.id)
    admin_session.add_all([advisor, worker])
    await admin_session.commit()
    return org, boss, advisor, worker


def _run(org: Org, agent: Agent, **over: object) -> AgentRun:
    base: dict = {
        "org_id": org.id,
        "agent_id": agent.id,
        "provider": agent.provider,
        "model": agent.model,
        "trigger": "work_order",
        "status": "done",
        "input": {"task": "x"},
        "finished_at": T0 + timedelta(minutes=5),
    }
    base.update(over)
    return AgentRun(**base)


class TestInteractionMap:
    async def test_one_lane_per_agent_carrying_its_icon(self, admin_session: AsyncSession) -> None:
        """The map is scanned, not read: the emoji is what makes a lane
        identifiable without parsing its name."""
        org, boss, advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Audit", assigned_agent_id=boss.id)
        await admin_session.flush()
        parent = _run(org, boss, work_order_id=wo.id, status="waiting", finished_at=None)
        admin_session.add(parent)
        await admin_session.flush()
        admin_session.add(_run(org, advisor, work_order_id=wo.id, parent_run_id=parent.id, trigger="consult"))
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        by_key = {ln.label: ln for ln in graph.lanes}
        assert set(by_key) == {"chief", "devils-advocate"}
        assert by_key["chief"].avatar == "🧭"
        assert by_key["chief"].agent_kind == "coordinator"

    async def test_a_run_opens_and_closes_its_lane(self, admin_session: AsyncSession) -> None:
        """A run still in flight gets no closing event — the lane simply ending is
        what makes "still going" visible without a spinner."""
        org, boss, advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Timing", assigned_agent_id=boss.id)
        await admin_session.flush()
        admin_session.add_all(
            [
                _run(org, boss, work_order_id=wo.id, status="running", finished_at=None),
                _run(org, advisor, work_order_id=wo.id, trigger="consult", status="done"),
            ]
        )
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        kinds = {(ln.label, e.kind) for ln in graph.lanes for e in graph.events if e.lane == ln.key}
        assert ("chief", "started") in kinds
        assert ("chief", "finished") not in kinds
        assert ("devils-advocate", "finished") in kinds

    async def test_a_consult_is_two_events_so_the_block_has_a_duration(self, admin_session: AsyncSession) -> None:
        """Collapsing the ask and the reply into one event would hide exactly the
        interval you want to see."""
        org, boss, advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Consulting", assigned_agent_id=boss.id)
        await admin_session.flush()
        parent = _run(org, boss, work_order_id=wo.id, status="waiting", finished_at=None)
        admin_session.add(parent)
        await admin_session.flush()
        peer = _run(org, advisor, work_order_id=wo.id, parent_run_id=parent.id, trigger="consult")
        admin_session.add(peer)
        await admin_session.flush()
        admin_session.add(
            AgentQuestion(
                org_id=org.id,
                run_id=parent.id,
                tool_call_id="call_1",
                asked_by_agent_id=boss.id,
                target_agent_id=advisor.id,
                peer_run_id=peer.id,
                question="What should I check?",
                answer="These things.",
                audience="agent",
                status="answered",
                answered_at=T0 + timedelta(minutes=2),
                work_order_id=wo.id,
            )
        )
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        ask = next(e for e in graph.events if e.kind == "consulted")
        reply = next(e for e in graph.events if e.kind == "answered")
        assert ask.lane == str(boss.id)
        assert ask.target_lane == str(advisor.id)
        # The reply comes back the other way, from the peer's lane.
        assert reply.lane == str(advisor.id)
        assert reply.target_lane == str(boss.id)
        assert reply.at > ask.at

    async def test_a_pending_approval_points_at_the_human_lane(self, admin_session: AsyncSession) -> None:
        """An approval is the one state a person can clear, so it must be drawn as
        something owed by *you* rather than as another agent status."""
        org, boss, _advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Needs a yes", assigned_agent_id=boss.id)
        await admin_session.flush()
        parent = _run(org, boss, work_order_id=wo.id, status="waiting", finished_at=None)
        admin_session.add(parent)
        await admin_session.flush()
        admin_session.add(
            AgentApproval(
                org_id=org.id,
                run_id=parent.id,
                tool_name="delegate_task",
                arguments={"agent": "research-analyst"},
                status="pending",
            )
        )
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        blocked = next(e for e in graph.events if e.lane == str(boss.id) and e.kind == "blocked")
        assert blocked.target_lane == "human"
        # A matching card in the human lane, so the arrow joins two things rather
        # than trailing off into an empty row.
        waiting = next(e for e in graph.events if e.lane == "human")
        assert waiting.title.startswith("approve ")
        assert "delegate_task" in blocked.title
        assert any(ln.key == "human" for ln in graph.lanes)

    async def test_no_human_lane_when_nobody_is_owed_anything(self, admin_session: AsyncSession) -> None:
        """An empty "you" track on every map is noise."""
        org, boss, _advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Self contained", assigned_agent_id=boss.id)
        await admin_session.flush()
        admin_session.add(_run(org, boss, work_order_id=wo.id))
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        assert [ln.key for ln in graph.lanes] == [str(boss.id)]

    async def test_a_blocked_lane_reads_as_blocked_even_after_earlier_success(
        self, admin_session: AsyncSession
    ) -> None:
        """Worst-first roll-up: the parked run is the one that still needs a person."""
        org, boss, _advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Twice", assigned_agent_id=boss.id)
        await admin_session.flush()
        admin_session.add_all(
            [
                _run(org, boss, work_order_id=wo.id, status="done"),
                _run(org, boss, work_order_id=wo.id, status="waiting", finished_at=None),
            ]
        )
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        assert graph.lanes[0].status == "waiting"

    async def test_events_are_in_time_order(self, admin_session: AsyncSession) -> None:
        org, boss, advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Ordered", assigned_agent_id=boss.id)
        await admin_session.flush()
        admin_session.add_all(
            [
                _run(org, boss, work_order_id=wo.id),
                _run(org, advisor, work_order_id=wo.id, trigger="consult"),
            ]
        )
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        assert [e.at for e in graph.events] == sorted(e.at for e in graph.events)

    async def test_an_order_that_never_ran_has_no_lanes(self, admin_session: AsyncSession) -> None:
        """The page hides the map entirely rather than drawing an empty grid."""
        org, boss, _advisor, _worker = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Nothing yet", assigned_agent_id=boss.id)
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        assert graph.lanes == []
        assert graph.events == []

    async def test_another_orgs_runs_are_not_reachable(self, admin_session: AsyncSession) -> None:
        """Defence in depth on top of RLS, matching the rest of the repos."""
        org_a, boss_a, _adv, _wrk = await _seed(admin_session)
        org_b, boss_b, _adv_b, _wrk_b = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org_a.id)
        wo = await svc.create_work_order(title="Mine", assigned_agent_id=boss_a.id)
        await admin_session.flush()
        # Same work_order_id, different org — must not appear.
        admin_session.add_all([_run(org_b, boss_b, work_order_id=wo.id), _run(org_a, boss_a, work_order_id=wo.id)])
        await admin_session.commit()

        graph = await svc.interaction_map(wo.id)

        assert len(graph.lanes) == 1
