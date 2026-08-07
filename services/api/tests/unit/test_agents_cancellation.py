"""Unit tests for cooperative cancellation in the agent loop (stubbed provider)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from api.models.agent import Agent
from api.services.agents.llm.provider import Completion, TextDelta, ToolCallRequest
from api.services.agents.runtime import RunCancelled, run_agent_loop
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

pytestmark = pytest.mark.unit


class _FakeProvider:
    def __init__(self, turns):
        self._turns = list(turns)

    async def stream(self, **_kwargs) -> AsyncIterator:
        deltas, completion = self._turns.pop(0)
        for d in deltas:
            yield TextDelta(d)
        yield completion


def _agent() -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind="operator", grants={})


def _ctx(agent) -> ToolContext:
    return ToolContext(session=None, org_id=uuid4(), settings=None, agent=agent)


def _read_spec(ran: list) -> ToolSpec:
    async def handler(ctx, args):
        ran.append(args)
        return {"ok": True}

    return ToolSpec(
        name="lookup",
        description="lookup",
        parameters={"type": "object", "properties": {}},
        category=Category.READ,
        handler=handler,
        always_allowed=True,
    )


async def _emit(_ev):
    pass


@pytest.mark.asyncio
async def test_cancel_before_first_turn_streams_nothing():
    streamed: list = []

    class _Prov:
        async def stream(self, **_kwargs):
            streamed.append(True)
            yield Completion(content="x", finish_reason="stop")

    async def cancelled() -> bool:
        return False

    with pytest.raises(RunCancelled):
        await run_agent_loop(
            provider=_Prov(),
            agent=_agent(),
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=[],
            ctx=_ctx(_agent()),
            emit=_emit,
            max_iterations=4,
            continue_check=cancelled,
        )
    assert streamed == []


@pytest.mark.asyncio
async def test_cancel_landing_during_stream_stops_before_any_tool_executes():
    """A cancel that commits while the model is streaming must stop the run after
    gating but BEFORE phase-2 execution — zero side effects from that batch."""
    ran: list = []
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(ToolCallRequest(id="c1", name="lookup", arguments={}),),
                    finish_reason="tool_calls",
                ),
            )
        ]
    )
    checks = iter([True, False])  # loop-top: alive; post-gate: cancelled

    async def continue_check() -> bool:
        return next(checks)

    with pytest.raises(RunCancelled):
        await run_agent_loop(
            provider=provider,
            agent=_agent(),
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=[_read_spec(ran)],
            ctx=_ctx(_agent()),
            emit=_emit,
            max_iterations=4,
            continue_check=continue_check,
        )
    assert ran == []


@pytest.mark.asyncio
async def test_live_run_is_unaffected_by_continue_check():
    ran: list = []
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(ToolCallRequest(id="c1", name="lookup", arguments={}),),
                    finish_reason="tool_calls",
                ),
            ),
            (["Done."], Completion(content="Done.", finish_reason="stop")),
        ]
    )

    async def alive() -> bool:
        return True

    result = await run_agent_loop(
        provider=provider,
        agent=_agent(),
        model="m",
        messages=[{"role": "user", "content": "go"}],
        specs=[_read_spec(ran)],
        ctx=_ctx(_agent()),
        emit=_emit,
        max_iterations=4,
        continue_check=alive,
    )
    assert ran == [{}]
    assert result.final_content == "Done."
