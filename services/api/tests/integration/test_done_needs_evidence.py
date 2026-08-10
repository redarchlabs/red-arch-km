"""``done`` has to be backed by something.

Seen live: an SEO work order ended with nine steps marked done, an adversarial
review board passed, and the thing actually asked for — open this website and audit
it — never attempted. What was delivered was a document about how one would build a
crawler. Nothing in the system could tell the difference, because ``done`` was a
string an agent wrote about itself and no code path anywhere could disagree.

The rules here are lifted from a definition-of-done that already works in practice
(`redarchlabs-agents/docs/agent-org/definition-of-done.md`): *you never self-declare
done*, and the transition is refused rather than merely discouraged.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.org import Org
from api.models.work_order import WorkOrderArtifact
from api.services.agents.work_order_service import (
    DELIVERY_TASK_TITLE,
    WorkOrderService,
    WorkOrderValidationError,
    has_delivery_step,
    wants_deliverable,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_GOOD = "Fetched robots.txt and crawled 42 pages; CSV attached as crawl.csv."


async def _seed(admin_session: AsyncSession, *, tasks: list[str], title: str = "Tidy the inbox"):
    org = Org(name=f"Done-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="analyst", provider="openai", model="m", kind="advisory", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id)
    wo = await svc.create_work_order(title=title, assigned_agent_id=agent.id)
    wo.status = "in_progress"
    await svc.set_tasks(wo.id, [{"title": t, "sort_order": i} for i, t in enumerate(tasks)])
    await admin_session.flush()
    return org, wo, agent, svc


async def _attach(admin_session: AsyncSession, org: Org, wo_id: uuid.UUID) -> None:
    admin_session.add(WorkOrderArtifact(work_order_id=wo_id, kind="output", filename="crawl.csv", org_id=org.id))
    await admin_session.flush()


class TestSayWhatYouProduced:
    async def test_done_without_evidence_is_refused(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])

        with pytest.raises(WorkOrderValidationError) as exc:
            await svc.update_task_status(wo.id, "T1", "done", agent=agent)

        assert "evidence" in str(exc.value)
        assert (await svc.list_tasks(wo.id))[0].status == "pending"

    @pytest.mark.parametrize("said", ["done", "ok", "finished", "completed the task", "   "])
    async def test_a_word_is_not_evidence(self, admin_session: AsyncSession, said: str) -> None:
        """Most of what an unforced field receives. A model that must name the output
        and where it is claims far fewer steps it did not take."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])

        with pytest.raises(WorkOrderValidationError):
            await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=said)

    async def test_real_evidence_goes_through_and_into_the_diary(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert (await svc.list_tasks(wo.id))[0].status == "done"
        # The person watching sees the claim, not just the tick.
        assert any(_GOOD in e.text for e in await svc.list_entries(wo.id))

    @pytest.mark.parametrize("status", ["in_progress", "blocked", "carried", "pending"])
    async def test_only_done_is_gated(self, admin_session: AsyncSession, status: str) -> None:
        """Every other transition is the agent working, and demanding evidence to say
        "I have started" is the bureaucracy that gets a rule routed around."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])
        if status == "pending":
            # Every task starts pending, and re-writing a status a step already has is
            # refused as a no-op — so reaching pending has to be a real move.
            await svc.update_task_status(wo.id, "T1", "in_progress", agent=agent)

        await svc.update_task_status(wo.id, "T1", status, agent=agent)

        assert (await svc.list_tasks(wo.id))[0].status == status


class TestTheOrderCannotCloseOnNothing:
    async def test_the_last_step_needs_something_attached(self, admin_session: AsyncSession) -> None:
        # The exact shape of the real failure: every step done, nothing to open.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"], title="Audit the site")

        with pytest.raises(WorkOrderValidationError) as exc:
            await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert "nothing attached" in str(exc.value)

    async def test_an_attachment_lets_it_close(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["A"], title="Audit the site")
        await _attach(admin_session, org, wo.id)

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert (await svc.list_tasks(wo.id))[0].status == "done"

    async def test_earlier_steps_are_not_gated_on_a_deliverable(self, admin_session: AsyncSession) -> None:
        """Only the last one. Plenty of real steps produce no file, and requiring one
        per task would be satisfied by attaching junk."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B", "C"])

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)
        await svc.update_task_status(wo.id, "T2", "done", agent=agent, evidence=_GOOD)

        assert [t.status for t in await svc.list_tasks(wo.id)] == ["done", "done", "pending"]

    async def test_carried_steps_still_count_as_closing_the_order(self, admin_session: AsyncSession) -> None:
        """`carried` is a decision not to do it here, so an order of one done step and
        one carried is finished — and still owes a deliverable."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"], title="Audit the site")
        await svc.update_task_status(wo.id, "T2", "carried", agent=agent)

        with pytest.raises(WorkOrderValidationError) as exc:
            await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert "nothing attached" in str(exc.value)

    async def test_an_order_that_only_wants_an_opinion_closes_without_a_file(self, admin_session: AsyncSession) -> None:
        """The real brief was "Check out SEO on redarchlabs.com and tell me what you
        think" — answered by an answer. Demanding an attachment there makes an agent
        produce a document nobody asked for purely to satisfy a check, which is the
        same drift-into-paperwork this whole area exists to stop."""
        org, wo, agent, svc = await _seed(
            admin_session, tasks=["A"], title="Check out SEO on redarchlabs.com and tell me what you think"
        )

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert (await svc.list_tasks(wo.id))[0].status == "done"

    async def test_a_plan_that_promised_delivery_is_held_to_it(self, admin_session: AsyncSession) -> None:
        """Even for an opinion order: if the agent's own plan says it will attach
        something, closing with nothing attached is it breaking its own word."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["x"], title="Tell me what you think")
        await svc.set_tasks(wo.id, [{"title": "Attach the findings", "sort_order": 0}])

        with pytest.raises(WorkOrderValidationError):
            await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

    async def test_a_blocked_step_left_open_is_not_the_last_one(self, admin_session: AsyncSession) -> None:
        # The order is not closing, so there is nothing to demand a deliverable for.
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B"])
        await svc.update_task_status(wo.id, "T2", "blocked", agent=agent)

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert (await svc.list_tasks(wo.id))[0].status == "done"


class TestThePlanOwesADelivery:
    @pytest.mark.parametrize(
        "brief",
        [
            "Audit the SEO on redarchlabs.com",
            "Write up a summary of Q3 support tickets",
            "Produce a CSV export of active vendors",
            "Draft a design for the new onboarding flow",
        ],
    )
    def test_it_recognises_a_promised_output(self, brief: str) -> None:
        assert wants_deliverable(brief)

    @pytest.mark.parametrize(
        "brief",
        ["What do you think of our pricing?", "Tidy the shared inbox", "Tell me if the site is down"],
    )
    def test_an_opinion_owes_a_reply_not_a_file(self, brief: str) -> None:
        # Adding a delivery step here is the bureaucracy that teaches people to
        # ignore the plan.
        assert not wants_deliverable(brief)

    def test_it_spots_a_plan_that_already_delivers(self) -> None:
        assert has_delivery_step(["Crawl", "Attach the CSV to the order"])
        assert not has_delivery_step(["Crawl", "Analyse", "Write findings"])

    async def test_a_plan_for_a_deliverable_gains_a_delivery_step(self, admin_session: AsyncSession) -> None:
        """A plan that produces a report and never says "hand it over" ends with the
        report inside the agent's own transcript — the same as never writing it."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["x"], title="Audit the site")

        tasks = await svc.set_tasks(
            wo.id, [{"title": "Crawl", "sort_order": 0}, {"title": "Analyse", "sort_order": 1}], add_delivery_step=True
        )

        assert [t.title for t in tasks][:2] == ["Crawl", "Analyse"]
        assert tasks[-1].title == DELIVERY_TASK_TITLE

    async def test_an_agent_that_planned_delivery_itself_gets_no_duplicate(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["x"], title="Audit the site")

        tasks = await svc.set_tasks(
            wo.id,
            [{"title": "Crawl", "sort_order": 0}, {"title": "Attach the report", "sort_order": 1}],
            add_delivery_step=True,
        )

        assert [t.title for t in tasks] == ["Crawl", "Attach the report"]

    async def test_re_planning_does_not_stack_delivery_steps(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["x"], title="Audit the site")

        await svc.set_tasks(wo.id, [{"title": "Crawl", "sort_order": 0}], add_delivery_step=True)
        tasks = await svc.set_tasks(wo.id, [{"title": "Crawl again", "sort_order": 0}], add_delivery_step=True)

        assert len([t for t in tasks if t.title == DELIVERY_TASK_TITLE]) == 1

    async def test_an_opinion_order_keeps_the_plan_it_was_given(self, admin_session: AsyncSession) -> None:
        org, wo, agent, svc = await _seed(admin_session, tasks=["x"])

        tasks = await svc.set_tasks(wo.id, [{"title": "Read it", "sort_order": 0}], add_delivery_step=False)

        assert [t.title for t in tasks] == ["Read it"]
