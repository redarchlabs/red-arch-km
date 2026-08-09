"""An advisory agent may plan. It still may not act.

Planning is saying what you *would* do, not doing it — the category covers task
lists, diary entries, submitting a plan for approval, and a workflow step's own
completion contract. Barring it did not make anything safer; it made the roster's
own advisers unable to do their jobs.
"""

from __future__ import annotations

import pytest
from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.tools.bridge import workflow_bridge_specs
from api.services.agents.tools.spec import Category, ToolSpec
from api.services.agents.tools.work_order_tasks import LIST_WORK_ORDER_TASKS, SET_WORK_ORDER_TASKS

pytestmark = pytest.mark.unit


async def _noop(ctx, args):  # pragma: no cover - specs only
    return {}


def _spec(name: str, category: str, *, side_effecting: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        category=category,
        handler=_noop,
        side_effecting=side_effecting,
    )


def _advisory() -> Agent:
    return Agent(
        name="solution-architect",
        kind="advisory",
        provider="openai",
        model="m",
        # Granted everything on purpose: the kind gate runs before grants, so this
        # proves the gate is what decides, not the roster.
        grants={"tools": ["update_record", "run_workflow"], "records_write": True},
    )


class TestAnAdvisoryAgentMayPlan:
    def test_it_can_write_the_task_list_it_is_asked_for(self) -> None:
        for spec in (SET_WORK_ORDER_TASKS, LIST_WORK_ORDER_TASKS):
            assert decide(_advisory(), spec).decision is not Decision.DENY

    def test_it_can_finish_a_workflow_step(self) -> None:
        """complete_task is PLAN and always_allowed — meant for every agent. The
        gate ran first and denied it, so an advisory agent driving a workflow step
        did the work and then could not report it; the step escalated or timed out.
        """
        complete, escalate = workflow_bridge_specs({})

        assert decide(_advisory(), complete).decision is not Decision.DENY
        assert decide(_advisory(), escalate).decision is not Decision.DENY

    def test_it_can_submit_a_plan_for_approval(self) -> None:
        # The scenario this exists for: an architect presenting a design to a board.
        from api.services.agents.tools.plan_mode import SUBMIT_PLAN

        assert decide(_advisory(), SUBMIT_PLAN).decision is not Decision.DENY


class TestItStillMayNotAct:
    def test_writing_a_record_is_still_refused(self) -> None:
        verdict = decide(_advisory(), _spec("update_record", Category.WRITE))

        assert verdict.decision is Decision.DENY
        assert "advisory" in verdict.reason

    def test_executing_is_still_refused(self) -> None:
        verdict = decide(_advisory(), _spec("run_workflow", Category.EXECUTE, side_effecting=True))

        assert verdict.decision is Decision.DENY

    def test_it_still_cannot_hand_work_to_anyone(self) -> None:
        """Advising is not commanding: delegation would let an adviser start work
        it is not accountable for."""
        assert decide(_advisory(), _spec("delegate_task", Category.DELEGATE)).decision is Decision.DENY

    def test_a_coordinator_still_cannot_write(self) -> None:
        # The other half of the gate is untouched.
        coordinator = Agent(
            name="tpm",
            kind="coordinator",
            provider="openai",
            model="m",
            grants={"tools": ["update_record"], "records_write": True},
        )

        assert decide(coordinator, _spec("update_record", Category.WRITE)).decision is Decision.DENY
