"""Unit tests for the agent tool-calling loop (stubbed provider + tools)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from api.models.agent import Agent
from api.services.agents.runtime import RunParked, run_agent_loop
from api.services.agents.llm.provider import Completion, ToolCallRequest, Usage
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

pytestmark = pytest.mark.unit


class _FakeProvider:
    """Yields a scripted sequence of turns; one turn consumed per stream() call."""

    def __init__(self, turns):
        self._turns = list(turns)

    async def stream(self, **_kwargs) -> AsyncIterator:
        deltas, completion = self._turns.pop(0)
        for d in deltas:
            from api.services.agents.llm.provider import TextDelta

            yield TextDelta(d)
        yield completion


def _agent(**grants) -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind="operator", grants=grants)


def _ctx(agent) -> ToolContext:
    return ToolContext(session=None, org_id=uuid4(), settings=None, agent=agent)


def _echo_spec(calls: list) -> ToolSpec:
    async def handler(ctx, args):
        calls.append(args)
        return {"echoed": args}

    return ToolSpec(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"x": {"type": "number"}}},
        category=Category.READ,
        handler=handler,
        always_allowed=True,
    )


async def _collect_emit():
    events = []

    async def emit(ev):
        events.append(ev)

    return events, emit


@pytest.mark.asyncio
async def test_loop_runs_tool_then_finishes():
    agent = _agent()
    calls: list = []
    provider = _FakeProvider(
        [
            (
                ["Let me ", "check."],
                Completion(
                    content="Let me check.",
                    tool_calls=(ToolCallRequest(id="c1", name="echo", arguments={"x": 1}),),
                    finish_reason="tool_calls",
                    usage=Usage(5, 2, 7),
                ),
            ),
            (["Done."], Completion(content="Done.", finish_reason="stop", usage=Usage(3, 1, 4))),
        ]
    )
    events, emit = await _collect_emit()

    result = await run_agent_loop(
        provider=provider,
        agent=agent,
        model="gpt-5-mini",
        messages=[{"role": "user", "content": "hi"}],
        specs=[_echo_spec(calls)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=8,
    )

    assert calls == [{"x": 1}]
    assert result.final_content == "Done."
    assert result.tool_calls == 1
    assert result.total_tokens == 11
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types and types[-1] == "done"
    # A tool result message was fed back to the model.
    assert any(m.get("role") == "tool" for m in result.messages)


@pytest.mark.asyncio
async def test_denied_tool_returns_error_without_calling_handler():
    agent = _agent()  # no grant for the execute tool
    handler_calls: list = []

    async def handler(ctx, args):
        handler_calls.append(args)
        return {"ok": True}

    exec_spec = ToolSpec(
        name="run_workflow",
        description="run",
        parameters={"type": "object", "properties": {}},
        category=Category.EXECUTE,
        handler=handler,
        side_effecting=True,
    )
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(ToolCallRequest(id="c1", name="run_workflow", arguments={}),),
                    finish_reason="tool_calls",
                ),
            ),
            (["stopping"], Completion(content="stopping", finish_reason="stop")),
        ]
    )
    events, emit = await _collect_emit()
    await run_agent_loop(
        provider=provider, agent=agent, model="m", messages=[{"role": "user", "content": "go"}],
        specs=[exec_spec], ctx=_ctx(agent), emit=emit, max_iterations=4,
    )
    assert handler_calls == []  # never executed
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert "Not permitted" in tool_results[0]["result"]["error"]


@pytest.mark.asyncio
async def test_parks_before_executing_any_tool_in_the_turn():
    """A turn with an allowed tool AND an ASK tool must park without running the
    allowed one — no partial side effects before human approval."""
    agent = _agent(tools=["run_workflow"], approval_required=["run_workflow"])
    ran: list = []

    def _tool(name, category, approve_gated=False):
        async def handler(ctx, args):
            ran.append(name)
            return {"ok": name}

        return ToolSpec(name=name, description=name, parameters={"type": "object", "properties": {}},
                        category=category, handler=handler, always_allowed=(category == Category.READ),
                        side_effecting=(category != Category.READ))

    provider = _FakeProvider([([], Completion(
        content="",
        tool_calls=(
            ToolCallRequest(id="c1", name="echo_read", arguments={}),
            ToolCallRequest(id="c2", name="run_workflow", arguments={}),
        ),
        finish_reason="tool_calls",
    ))])
    _events, emit = await _collect_emit()

    async def park(_spec, _args):
        raise RunParked("approval")

    with pytest.raises(RunParked):
        await run_agent_loop(
            provider=provider, agent=agent, model="m", messages=[{"role": "user", "content": "go"}],
            specs=[_tool("echo_read", Category.READ), _tool("run_workflow", Category.EXECUTE)],
            ctx=_ctx(agent), emit=emit, max_iterations=4, approval_strategy=park,
        )
    assert ran == []  # neither tool executed before the park


@pytest.mark.asyncio
async def test_ask_strategy_can_park_the_run():
    agent = _agent(tools=["run_workflow"], approval_required=["run_workflow"])
    spec = ToolSpec(
        name="run_workflow", description="run", parameters={"type": "object", "properties": {}},
        category=Category.EXECUTE, handler=lambda c, a: None, side_effecting=True,
    )
    provider = _FakeProvider(
        [([], Completion(content="", tool_calls=(ToolCallRequest(id="c1", name="run_workflow", arguments={}),), finish_reason="tool_calls"))]
    )
    _events, emit = await _collect_emit()

    async def park_strategy(_spec, _args):
        raise RunParked("approval", {"tool": "run_workflow"})

    with pytest.raises(RunParked) as exc:
        await run_agent_loop(
            provider=provider, agent=agent, model="m", messages=[{"role": "user", "content": "go"}],
            specs=[spec], ctx=_ctx(agent), emit=emit, max_iterations=4,
            approval_strategy=park_strategy,
        )
    assert exc.value.wait_kind == "approval"
