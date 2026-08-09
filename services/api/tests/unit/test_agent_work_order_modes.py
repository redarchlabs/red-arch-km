"""Plan / manual / automatic — how much rope an agent gets on one work order.

``orgs.agent_autonomy`` already gated side-effecting tools, but it is org-wide and
nothing ever set it. The choice people actually want is per job: think this one
through before touching anything, or get on with it and stop asking me.

Enforced in the authority gate rather than the prompt, because a prompt is a
request and this has to be a guarantee — a plan-mode order must not be able to
change anything whatever the model decides to try.
"""

from __future__ import annotations

import pytest
from api.models.agent import Agent
from api.services.agents.authority import Decision, Posture, available_tools, decide, posture_for
from api.services.agents.tools.spec import Category, ToolSpec

pytestmark = pytest.mark.unit


async def _noop(ctx, args):  # pragma: no cover - never invoked; specs only
    return {}


def _spec(name: str, category: str, *, side_effecting: bool = False, always_allowed: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        category=category,
        handler=_noop,
        side_effecting=side_effecting,
        always_allowed=always_allowed,
    )


READ = _spec("search_knowledge", Category.READ, always_allowed=True)
PLAN = _spec("set_work_order_tasks", Category.PLAN)
DELEGATE = _spec("delegate_task", Category.DELEGATE)
WRITE = _spec("update_record", Category.WRITE)
EXECUTE = _spec("run_workflow", Category.EXECUTE, side_effecting=True)


def _operator(**grants) -> Agent:
    return Agent(
        name="op",
        kind="operator",
        provider="openai",
        model="gpt-5-mini",
        grants={"tools": ["update_record", "run_workflow"], "records_write": True, **grants},
    )


class TestPlanMode:
    def test_it_can_still_read_plan_and_delegate(self) -> None:
        agent = _operator()

        for spec in (READ, PLAN, DELEGATE):
            assert decide(agent, spec, autonomy=Posture.PLAN_ONLY).decision is Decision.ALLOW

    def test_writing_and_executing_are_denied(self) -> None:
        agent = _operator()

        for spec in (WRITE, EXECUTE):
            verdict = decide(agent, spec, autonomy=Posture.PLAN_ONLY)
            assert verdict.decision is Decision.DENY
            assert "plan mode" in verdict.reason

    def test_the_refusal_points_at_the_way_out(self) -> None:
        """A bare refusal is a dead end, and an agent that only gets refused reports
        that as a failure instead of delivering the plan it was asked for."""
        agent = _operator()

        verdict = decide(agent, WRITE, autonomy=Posture.PLAN_ONLY)
        assert verdict.decision is Decision.DENY
        assert "submit_plan" in verdict.reason

    def test_the_model_is_never_shown_a_tool_it_cannot_use(self) -> None:
        # Offering them burns turns proposing actions that can never happen.
        names = [s.name for s in available_tools(_operator(), [READ, PLAN, WRITE, EXECUTE], autonomy=Posture.PLAN_ONLY)]

        assert names == ["search_knowledge", "set_work_order_tasks"]


class TestAutomaticMode:
    def test_it_grants_what_would_otherwise_be_asked(self) -> None:
        agent = _operator(approval_required=["update_record"])

        assert decide(agent, WRITE, autonomy=Posture.AUTOMATIC).decision is Decision.ALLOW
        assert decide(agent, EXECUTE, autonomy=Posture.AUTOMATIC).decision is Decision.ALLOW

    def test_it_does_not_widen_what_the_agent_may_touch(self) -> None:
        """Automatic removes the human from the loop; it is not a grant. An agent
        without the grant still cannot use the tool, or 'automatic' would quietly
        mean 'everything'."""
        agent = Agent(name="op", kind="operator", provider="openai", model="m", grants={"tools": []})

        assert decide(agent, WRITE, autonomy=Posture.AUTOMATIC).decision is Decision.DENY

    def test_the_kind_gate_still_holds(self) -> None:
        """A coordinator may not write in any mode — that is a role restriction,
        not an approval one."""
        coordinator = Agent(
            name="coo",
            kind="coordinator",
            provider="openai",
            model="m",
            grants={"tools": ["update_record"], "records_write": True},
        )

        assert decide(coordinator, WRITE, autonomy=Posture.AUTOMATIC).decision is Decision.DENY


class TestManualIsUnchanged:
    def test_it_behaves_exactly_as_before(self) -> None:
        agent = _operator(approval_required=["update_record"])

        assert decide(agent, WRITE, autonomy="high_touch").decision is Decision.ASK
        assert decide(agent, EXECUTE, autonomy="high_touch").decision is Decision.ASK
        assert decide(agent, READ, autonomy="high_touch").decision is Decision.ALLOW


class TestPostureResolution:
    class _WO:
        def __init__(self, mode: str) -> None:
            self.mode = mode

    def test_a_work_order_mode_wins(self) -> None:
        assert posture_for(self._WO("plan"), "high_touch") == Posture.PLAN_ONLY
        assert posture_for(self._WO("automatic"), "high_touch") == Posture.AUTOMATIC

    def test_manual_defers_to_the_org(self) -> None:
        """So an org that has moved off high_touch keeps that setting."""
        assert posture_for(self._WO("manual"), "balanced") == "balanced"

    def test_a_run_with_no_work_order_uses_the_org_posture(self) -> None:
        # Console chats and scheduled runs have no order and must be unaffected.
        assert posture_for(None, "high_touch") == "high_touch"
