"""Unit tests for the agent authority engine (kind-gate + deny/ask/allow)."""

from __future__ import annotations

import pytest

from api.models.agent import Agent
from api.services.agents.authority import (
    AuthorityVerdict,
    Decision,
    available_tools,
    decide,
)
from api.services.agents.kind_gate import kind_gate
from api.services.agents.tools.spec import Category, ToolSpec

pytestmark = pytest.mark.unit


async def _noop(ctx, args):  # pragma: no cover - handler never called in these tests
    return {}


def _spec(name, category, *, side_effecting=False, always_allowed=False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        category=category,
        handler=_noop,
        side_effecting=side_effecting,
        always_allowed=always_allowed,
    )


def _agent(kind, **grants) -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind=kind, grants=grants)


# --- kind-gate -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "category", "blocked"),
    [
        ("coordinator", Category.EXECUTE, True),
        ("coordinator", Category.WRITE, True),
        ("coordinator", Category.DELEGATE, False),
        ("coordinator", Category.READ, False),
        ("advisory", Category.DELEGATE, True),
        ("advisory", Category.WRITE, True),
        ("advisory", Category.EXECUTE, True),
        ("advisory", Category.ESCALATE, False),
        ("operator", Category.EXECUTE, False),
        ("operator", Category.WRITE, False),
    ],
)
def test_kind_gate(kind, category, blocked):
    denial = kind_gate(kind, _spec("t", category))
    assert (denial is not None) == blocked


# --- decide ----------------------------------------------------------------


def test_read_tool_always_allowed():
    v = decide(_agent("operator"), _spec("search", Category.READ, always_allowed=True))
    assert v.decision is Decision.ALLOW


def test_execute_denied_without_grant_then_allowed_with_grant():
    spec = _spec("run_workflow", Category.EXECUTE, side_effecting=True)
    assert decide(_agent("operator"), spec).decision is Decision.DENY
    assert decide(_agent("operator", tools=["run_workflow"]), spec).decision is Decision.ALLOW


def test_execute_asks_when_in_approval_list():
    spec = _spec("run_workflow", Category.EXECUTE, side_effecting=True)
    agent = _agent("operator", tools=["run_workflow"], approval_required=["run_workflow"])
    assert decide(agent, spec).decision is Decision.ASK


def test_write_needs_records_write_flag():
    spec = _spec("update_record", Category.WRITE, side_effecting=True)
    assert decide(_agent("operator", tools=["update_record"]), spec).decision is Decision.DENY
    granted = _agent("operator", tools=["update_record"], records_write=True)
    assert decide(granted, spec).decision is Decision.ALLOW


def test_kind_gate_beats_grant():
    # A coordinator granted run_workflow is still denied — role forbids EXECUTE.
    spec = _spec("run_workflow", Category.EXECUTE, side_effecting=True)
    agent = _agent("coordinator", tools=["run_workflow"])
    assert decide(agent, spec).decision is Decision.DENY


def test_role_provided_coordination_tools_available_by_kind():
    delegate = _spec("delegate_task", Category.DELEGATE)
    assert decide(_agent("coordinator"), delegate).decision is Decision.ALLOW
    assert decide(_agent("advisory"), delegate).decision is Decision.DENY  # kind-gated


def test_available_tools_excludes_denied():
    specs = [
        _spec("search", Category.READ, always_allowed=True),
        _spec("run_workflow", Category.EXECUTE),  # not granted -> denied
        _spec("delegate_task", Category.DELEGATE),
    ]
    names = {s.name for s in available_tools(_agent("coordinator"), specs)}
    assert names == {"search", "delegate_task"}
