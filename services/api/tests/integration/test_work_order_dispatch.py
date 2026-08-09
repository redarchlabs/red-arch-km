"""Starting a work order queues a run for the agent it is assigned to.

Before this, a work order was a folder that agent runs filed their diary into and
nothing more: assigning one to an agent recorded an intention that nothing acted
on, and moving it through the whole state machine changed a string. A work order
filed against an agent and left in ``in_progress`` forever looks identical to one
the agent is actively working — which is how a request to an agent gets silently
dropped.

``in_progress`` is the seam, because that status already claims work is happening.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.org import Org
from api.models.user import UserProfile
from api.schemas.work_order import WorkOrderRead
from api.services.agents.work_order_service import (
    WorkOrderService,
    WorkOrderValidationError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[Org, Agent, UserProfile]:
    tag = uuid.uuid4().hex[:8]
    org = Org(name=f"WO-{tag}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="chief", provider="openai", model="gpt-5-mini", kind="coordinator", org_id=org.id)
    profile = UserProfile(auth_subject=f"sub-{tag}", username=f"u-{tag}", email=f"u-{tag}@x.com")
    admin_session.add_all([agent, profile])
    await admin_session.commit()
    return org, agent, profile


async def _runs_for(session: AsyncSession, wo_id: uuid.UUID) -> list[AgentRun]:
    result = await session.execute(select(AgentRun).where(AgentRun.work_order_id == wo_id))
    return list(result.scalars().all())


class TestStatusResponseIsSerializable:
    """Every status change 500'd before this — no work order could leave `draft`.

    ``updated_at`` carries ``onupdate=func.now()``, a *server-side* default, so
    after the UPDATE flushes SQLAlchemy expires the attribute rather than guessing
    its value. Reading it then triggers a lazy refresh, and a lazy refresh from
    Pydantic's synchronous attribute walk is IO outside the greenlet context:
    ``MissingGreenlet``, surfaced to the browser as a bare "Internal server error".

    The transition itself always committed. Only the response blew up — which is
    the worst shape for this bug, because a retry then fails with "cannot move
    from 'approved' to 'approved'" and the state looks stuck rather than done.
    """

    async def test_set_status_result_can_be_serialized(self, admin_session: AsyncSession) -> None:
        org, _agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Serialize me")
        await admin_session.commit()

        updated = await svc.set_status(wo.id, "approved", actor_profile_id=profile.id)

        # What the route does with the returned object.
        assert WorkOrderRead.model_validate(updated).status == "approved"

    async def test_assign_result_can_be_serialized(self, admin_session: AsyncSession) -> None:
        """`assign` flushes an UPDATE and serializes the same instance too."""
        org, agent, _profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Assign me")
        await admin_session.commit()

        updated = await svc.assign(wo.id, agent.id)

        assert WorkOrderRead.model_validate(updated).assigned_agent_id == agent.id


class TestDispatchOnStart:
    async def test_starting_an_assigned_order_queues_a_run(self, admin_session: AsyncSession) -> None:
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(
            title="SRO on redarchlabs.com",
            body="Please do an SEO check on redarchlabs.com",
            assigned_agent_id=agent.id,
        )
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        runs = await _runs_for(admin_session, wo.id)
        assert len(runs) == 1
        run = runs[0]
        assert run.agent_id == agent.id
        # queued, not running: the existing advance-runs sweep drives it, so this
        # adds no second execution path.
        assert run.status == "queued"
        assert run.trigger == "work_order"
        # The title alone is not a task — the body carries the actual request.
        assert "SEO check on redarchlabs.com" in run.input["task"]
        assert "SRO on redarchlabs.com" in run.input["task"]

    async def test_the_run_acts_for_the_person_who_started_it(self, admin_session: AsyncSession) -> None:
        """Knowledge scoping reads with the run's actor. Without one, the agent is
        refused the knowledge base entirely (fail-closed), so a dispatched run with
        no actor would start and then be unable to read anything."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Audit", assigned_agent_id=agent.id)
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        assert (await _runs_for(admin_session, wo.id))[0].actor_user_id == profile.id

    async def test_approved_then_started_also_dispatches(self, admin_session: AsyncSession) -> None:
        """The gated route through the state machine, not just draft -> in_progress."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Gated", assigned_agent_id=agent.id)
        await admin_session.commit()

        await svc.set_status(wo.id, "awaiting_approval", actor_profile_id=profile.id)
        await svc.set_status(wo.id, "approved", actor_profile_id=profile.id)
        assert await _runs_for(admin_session, wo.id) == []  # approval is not a start

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        assert len(await _runs_for(admin_session, wo.id)) == 1

    async def test_an_unassigned_order_still_transitions_without_a_run(self, admin_session: AsyncSession) -> None:
        """A work order is also a human tracking artifact. Requiring an agent to
        start one would break every order people work themselves."""
        org, _agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Human work")
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        assert (await svc.get_work_order(wo.id)).status == "in_progress"
        assert await _runs_for(admin_session, wo.id) == []

    async def test_a_disabled_agent_is_refused_loudly(self, admin_session: AsyncSession) -> None:
        """Assigned-but-unrunnable is a broken configuration, not a human-only
        order. Transitioning anyway would leave the order claiming work is in
        progress that nothing will ever do — the exact failure this change exists
        to remove."""
        org, agent, profile = await _seed(admin_session)
        agent.enabled = False
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Dead end", assigned_agent_id=agent.id)
        await admin_session.commit()

        with pytest.raises(WorkOrderValidationError):
            await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)

        assert (await svc.get_work_order(wo.id)).status == "draft"
        assert await _runs_for(admin_session, wo.id) == []

    async def test_a_live_run_is_never_duplicated(self, admin_session: AsyncSession) -> None:
        """Two concurrent starts must not put two agents on the same job: that is
        duplicated side effects and two billed LLM runs."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Already going", assigned_agent_id=agent.id)
        await admin_session.flush()
        admin_session.add(
            AgentRun(
                org_id=org.id,
                agent_id=agent.id,
                work_order_id=wo.id,
                provider=agent.provider,
                model=agent.model,
                trigger="work_order",
                status="running",
                input={"task": "the first one"},
            )
        )
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        runs = await _runs_for(admin_session, wo.id)
        assert len(runs) == 1
        assert runs[0].input["task"] == "the first one"

    async def test_a_finished_run_does_not_block_a_restart(self, admin_session: AsyncSession) -> None:
        """The guard is against a *live* run, not against the order ever having
        run before."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Round two", assigned_agent_id=agent.id)
        await admin_session.flush()
        admin_session.add(
            AgentRun(
                org_id=org.id,
                agent_id=agent.id,
                work_order_id=wo.id,
                provider=agent.provider,
                model=agent.model,
                trigger="work_order",
                status="done",
                input={"task": "the old one"},
            )
        )
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        assert len(await _runs_for(admin_session, wo.id)) == 2

    async def test_the_dispatch_is_recorded_in_the_diary(self, admin_session: AsyncSession) -> None:
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Traceable", assigned_agent_id=agent.id)
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        entries = await svc.list_entries(wo.id)
        assert len(entries) == 1
        assert "chief" in entries[0].text
        assert entries[0].agent_run_id == (await _runs_for(admin_session, wo.id))[0].id


class TestAssigningAfterTheStart:
    """The failure this exists to stop, seen live: an order was filed with no
    agent (the create form deliberately omits the picker), started, and *then*
    assigned. Dispatch only ever fired on the status edge into ``in_progress``,
    which had already passed with nothing to dispatch — so the order sat
    ``in_progress``, with an agent, and no run, indefinitely.
    """

    async def test_assigning_a_started_order_dispatches_it(self, admin_session: AsyncSession) -> None:
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Assign me later")
        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()
        assert await _runs_for(admin_session, wo.id) == []

        await svc.assign(wo.id, agent.id, actor_profile_id=profile.id)
        await admin_session.commit()

        runs = await _runs_for(admin_session, wo.id)
        assert [r.agent_id for r in runs] == [agent.id]
        assert runs[0].status == "queued"

    async def test_assigning_a_draft_order_does_not_start_it(self, admin_session: AsyncSession) -> None:
        """Choosing who will do the work is not the decision to begin it."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Not yet")
        await admin_session.commit()

        await svc.assign(wo.id, agent.id, actor_profile_id=profile.id)
        await admin_session.commit()

        assert await _runs_for(admin_session, wo.id) == []

    async def test_reassigning_a_running_order_does_not_double_dispatch(self, admin_session: AsyncSession) -> None:
        """The live-run guard has to hold here too, or handing an order to someone
        else puts two agents on the same job."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Already going", assigned_agent_id=agent.id)
        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        await svc.assign(wo.id, agent.id, actor_profile_id=profile.id)
        await admin_session.commit()

        assert len(await _runs_for(admin_session, wo.id)) == 1

    async def test_unassigning_never_dispatches(self, admin_session: AsyncSession) -> None:
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Taking it back", assigned_agent_id=agent.id)
        await admin_session.commit()

        await svc.assign(wo.id, None, actor_profile_id=profile.id)
        await admin_session.commit()

        assert await _runs_for(admin_session, wo.id) == []

    async def test_starting_without_an_agent_says_so_in_the_diary(self, admin_session: AsyncSession) -> None:
        """Starting an order that will not run looks identical to starting one that
        will, and the order's own record is the only place anyone would look."""
        org, _agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Nobody home")
        await admin_session.commit()

        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        page = await svc.list_entries_page(wo.id)
        assert "no agent assigned" in page.entries[0].text


class TestMode:
    """plan | manual | automatic, chosen per job rather than per org."""

    async def test_a_new_order_is_manual(self, admin_session: AsyncSession) -> None:
        """Existing behaviour has to be the default: 'automatic' must never be
        something an order acquires by accident."""
        org, _agent, _profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)

        wo = await svc.create_work_order(title="Default")

        assert wo.mode == "manual"

    async def test_changing_mode_is_written_into_the_diary(self, admin_session: AsyncSession) -> None:
        """'automatic' means outbound actions stop asking anyone, so "who turned
        that off, and when" has to be answerable from the order itself."""
        org, _agent, _profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Going hands-off")
        await admin_session.commit()

        await svc.set_mode(wo.id, "automatic")
        await admin_session.commit()

        assert (await svc.get_work_order(wo.id)).mode == "automatic"
        assert "manual to automatic" in (await svc.list_entries_page(wo.id)).entries[-1].text

    async def test_setting_the_same_mode_writes_nothing(self, admin_session: AsyncSession) -> None:
        # A polling UI that re-sends the current mode must not fill the diary.
        org, _agent, _profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="No change")
        await admin_session.commit()

        await svc.set_mode(wo.id, "manual")
        await admin_session.commit()

        assert (await svc.list_entries_page(wo.id)).entries == []

    async def test_an_unknown_mode_is_refused(self, admin_session: AsyncSession) -> None:
        org, _agent, _profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Nope")
        await admin_session.commit()

        with pytest.raises(WorkOrderValidationError):
            await svc.set_mode(wo.id, "yolo")

    async def test_a_delegated_child_stays_on_the_same_order(self, admin_session: AsyncSession) -> None:
        """Plan mode is resolved from the run's work order, so a child that carries
        the order carries the mode. If delegation ever dropped work_order_id, a
        plan-mode coordinator could delegate its way into real actions."""
        org, agent, profile = await _seed(admin_session)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title="Parent", assigned_agent_id=agent.id, mode="plan")
        await svc.set_status(wo.id, "in_progress", actor_profile_id=profile.id)
        await admin_session.commit()

        runs = await _runs_for(admin_session, wo.id)

        assert [r.work_order_id for r in runs] == [wo.id]
