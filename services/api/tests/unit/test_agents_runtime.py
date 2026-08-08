"""Unit tests for the agent tool-calling loop (stubbed provider + tools)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from api.models.agent import Agent
from api.services.agents.llm.provider import Completion, ToolCallRequest, Usage
from api.services.agents.runtime import RunParked, run_agent_loop
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
        provider=provider,
        agent=agent,
        model="m",
        messages=[{"role": "user", "content": "go"}],
        specs=[exec_spec],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
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

        return ToolSpec(
            name=name,
            description=name,
            parameters={"type": "object", "properties": {}},
            category=category,
            handler=handler,
            always_allowed=(category == Category.READ),
            side_effecting=(category != Category.READ),
        )

    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(
                        ToolCallRequest(id="c1", name="echo_read", arguments={}),
                        ToolCallRequest(id="c2", name="run_workflow", arguments={}),
                    ),
                    finish_reason="tool_calls",
                ),
            )
        ]
    )
    _events, emit = await _collect_emit()

    async def park(_spec, _args):
        raise RunParked("approval")

    with pytest.raises(RunParked):
        await run_agent_loop(
            provider=provider,
            agent=agent,
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=[_tool("echo_read", Category.READ), _tool("run_workflow", Category.EXECUTE)],
            ctx=_ctx(agent),
            emit=emit,
            max_iterations=4,
            approval_strategy=park,
        )
    assert ran == []  # neither tool executed before the park


@pytest.mark.asyncio
async def test_ask_strategy_can_park_the_run():
    agent = _agent(tools=["run_workflow"], approval_required=["run_workflow"])
    spec = ToolSpec(
        name="run_workflow",
        description="run",
        parameters={"type": "object", "properties": {}},
        category=Category.EXECUTE,
        handler=lambda c, a: None,
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
            )
        ]
    )
    _events, emit = await _collect_emit()

    async def park_strategy(_spec, _args):
        raise RunParked("approval", {"tool": "run_workflow"})

    with pytest.raises(RunParked) as exc:
        await run_agent_loop(
            provider=provider,
            agent=agent,
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=[spec],
            ctx=_ctx(agent),
            emit=emit,
            max_iterations=4,
            approval_strategy=park_strategy,
        )
    assert exc.value.wait_kind == "approval"


def _blocking_spec(name: str, ran: list, wait_kind: str = "question") -> ToolSpec:
    """A tool that suspends the run from inside its handler, the way ask_human and
    consult_peer do — the block is the tool's purpose, not a verdict on it."""

    async def handler(ctx, args):
        ran.append(name)
        raise RunParked(wait_kind, {"question": args.get("question")})

    return ToolSpec(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {"question": {"type": "string"}}},
        category=Category.ESCALATE,
        handler=handler,
        always_allowed=True,
    )


@pytest.mark.asyncio
async def test_a_handler_that_parks_keeps_the_calls_that_have_not_run():
    """A mid-batch park must hand back exactly the unfinished work. Anything the
    turn already executed is recorded in ``messages``; listing it as pending too
    would run those side effects a second time on resume."""
    agent = _agent()
    ran: list = []
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(
                        ToolCallRequest(id="c1", name="echo", arguments={"x": 1}),
                        ToolCallRequest(id="c2", name="ask", arguments={"question": "Which region?"}),
                        ToolCallRequest(id="c3", name="echo", arguments={"x": 2}),
                    ),
                    finish_reason="tool_calls",
                ),
            )
        ]
    )
    _events, emit = await _collect_emit()
    echoed: list = []

    with pytest.raises(RunParked) as exc:
        await run_agent_loop(
            provider=provider,
            agent=agent,
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=[_echo_spec(echoed), _blocking_spec("ask", ran)],
            ctx=_ctx(agent),
            emit=emit,
            max_iterations=4,
        )

    assert exc.value.wait_kind == "question"
    # The first echo ran and must NOT be replayed; the blocked call and the one
    # after it must be.
    assert [p["id"] for p in exc.value.pending] == ["c2", "c3"]
    assert echoed == [{"x": 1}]
    # The executed call's result is already in the transcript being resumed.
    assert any(m.get("tool_call_id") == "c1" for m in exc.value.messages)


@pytest.mark.asyncio
async def test_an_answered_call_is_never_re_executed():
    """The whole point of resume_answers: the stored answer becomes the call's
    result. Re-running the handler would just ask the same question again."""
    agent = _agent()
    ran: list = []
    provider = _FakeProvider([([], Completion(content="Thanks.", finish_reason="stop"))])
    _events, emit = await _collect_emit()

    result = await run_agent_loop(
        provider=provider,
        agent=agent,
        model="m",
        messages=[{"role": "assistant", "content": ""}],
        specs=[_blocking_spec("ask", ran)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
        resume_tool_calls=[ToolCallRequest(id="c2", name="ask", arguments={"question": "Which region?"})],
        resume_answers={"c2": {"answer": "us-east-1"}},
    )

    assert ran == []  # the handler never fired
    assert result.final_content == "Thanks."
    # The answer reached the model as that call's tool result.
    tool_msg = next(m for m in result.messages if m.get("tool_call_id") == "c2")
    assert "us-east-1" in tool_msg["content"]


@pytest.mark.asyncio
async def test_an_answered_call_skips_the_authority_gate_too():
    """The tool already ran to the point of blocking; there is nothing left to
    permit. Re-gating an answered call would park it again for approval and strand
    the answer a human already gave."""
    agent = _agent(tools=["ask"], approval_required=["ask"])
    ran: list = []
    provider = _FakeProvider([([], Completion(content="ok", finish_reason="stop"))])
    _events, emit = await _collect_emit()
    asked: list = []

    async def park_strategy(_spec, _args):
        asked.append(_spec.name)
        raise RunParked("approval")

    result = await run_agent_loop(
        provider=provider,
        agent=agent,
        model="m",
        messages=[{"role": "assistant", "content": ""}],
        specs=[_blocking_spec("ask", ran)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
        approval_strategy=park_strategy,
        resume_tool_calls=[ToolCallRequest(id="c2", name="ask", arguments={})],
        resume_answers={"c2": {"answer": "yes"}},
    )

    assert asked == [] and ran == []
    assert result.final_content == "ok"


@pytest.mark.asyncio
async def test_the_executing_call_id_is_visible_to_the_handler():
    """A parking handler addresses its answer back to the exact call that blocked,
    so it has to be able to see which call it is."""
    agent = _agent()
    seen: list = []

    async def handler(ctx, args):
        seen.append(ctx.tool_call_id)
        return {"ok": True}

    spec = ToolSpec(
        name="peek",
        description="peek",
        parameters={"type": "object", "properties": {}},
        category=Category.READ,
        handler=handler,
        always_allowed=True,
    )
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(ToolCallRequest(id="call_abc", name="peek", arguments={}),),
                    finish_reason="tool_calls",
                ),
            ),
            ([], Completion(content="done", finish_reason="stop")),
        ]
    )
    _events, emit = await _collect_emit()

    await run_agent_loop(
        provider=provider,
        agent=agent,
        model="m",
        messages=[{"role": "user", "content": "go"}],
        specs=[spec],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
    )

    assert seen == ["call_abc"]
