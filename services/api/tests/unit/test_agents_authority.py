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
    # Grant mechanics in isolation (hands_off posture, so side-effecting isn't force-gated).
    spec = _spec("run_workflow", Category.EXECUTE, side_effecting=True)
    assert decide(_agent("operator"), spec, autonomy="hands_off").decision is Decision.DENY
    assert (
        decide(_agent("operator", tools=["run_workflow"]), spec, autonomy="hands_off").decision
        is Decision.ALLOW
    )


def test_execute_asks_when_in_approval_list():
    spec = _spec("run_workflow", Category.EXECUTE, side_effecting=True)
    agent = _agent("operator", tools=["run_workflow"], approval_required=["run_workflow"])
    assert decide(agent, spec).decision is Decision.ASK


def test_write_needs_records_write_flag():
    spec = _spec("update_record", Category.WRITE, side_effecting=True)
    assert decide(_agent("operator", tools=["update_record"]), spec).decision is Decision.DENY
    granted = _agent("operator", tools=["update_record"], records_write=True)
    # hands_off isolates the records_write mechanic from the high-touch overlay.
    assert decide(granted, spec, autonomy="hands_off").decision is Decision.ALLOW


# --- high-touch autonomy overlay ------------------------------------------


def test_high_touch_forces_ask_on_side_effecting_action():
    # A granted, allowed EXECUTE tool is still gated to ASK under high_touch,
    # even without being listed in approval_required.
    spec = _spec("send_email", Category.EXECUTE, side_effecting=True)
    agent = _agent("operator", tools=["send_email"])
    assert decide(agent, spec, autonomy="high_touch").decision is Decision.ASK
    assert decide(agent, spec, autonomy="hands_off").decision is Decision.ALLOW


def test_high_touch_does_not_gate_internal_writes():
    # Record/document writes are side_effecting=False → never force-gated.
    spec = _spec("create_record", Category.WRITE, side_effecting=False)
    agent = _agent("operator", tools=["create_record"], records_write=True)
    assert decide(agent, spec, autonomy="high_touch").decision is Decision.ALLOW


def test_mcp_server_wildcard_grant():
    agent = _agent("operator", tools=["mcp__perplexity__*"])
    # A read-only search tool under the server wildcard runs even under high-touch.
    search = _spec("mcp__perplexity__search", Category.EXECUTE, side_effecting=False)
    assert decide(agent, search, autonomy="high_touch").decision is Decision.ALLOW
    # A side-effecting tool under the same wildcard still asks under high-touch.
    post = _spec("mcp__perplexity__post", Category.EXECUTE, side_effecting=True)
    assert decide(agent, post, autonomy="high_touch").decision is Decision.ASK
    # Without any grant, the MCP tool is denied.
    assert decide(_agent("operator"), search).decision is Decision.DENY


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
