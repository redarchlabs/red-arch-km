"""Compaction as the loop actually applies it.

The pure rules are covered in ``test_agents_transcript_compaction``. What matters
here is the wiring, and one invariant in particular: the *emitted* event — which is
what becomes the persisted run step — must carry the full result even when the
message the model re-reads does not. Get that backwards and the feature stops being
a cost saving and becomes data loss.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from api.models.agent import Agent
from api.services.agents.llm.provider import Completion, TextDelta, ToolCallRequest
from api.services.agents.runtime import run_agent_loop
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

pytestmark = pytest.mark.unit


class _FakeProvider:
    """A scripted turn sequence, plus an overridable single-shot for the summarizer."""

    def __init__(self, turns, summary: str | None = None, summary_error: Exception | None = None) -> None:
        self._turns = list(turns)
        self._summary = summary
        self._summary_error = summary_error

    async def stream(self, **_kwargs) -> AsyncIterator:
        deltas, completion = self._turns.pop(0)
        for delta in deltas:
            yield TextDelta(delta)
        yield completion

    async def complete(self, **_kwargs) -> Completion:
        if self._summary_error is not None:
            raise self._summary_error
        return Completion(content=self._summary or "", finish_reason="stop")


def _agent() -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind="operator", grants={})


def _ctx(agent: Agent) -> ToolContext:
    return ToolContext(session=None, org_id=uuid4(), settings=None, agent=agent)


def _fat_spec(size: int) -> ToolSpec:
    async def handler(_ctx, _args):
        return {"status": "ok", "body": "x" * size}

    return ToolSpec(
        name="fat",
        description="returns a large result",
        parameters={"type": "object", "properties": {}},
        category=Category.READ,
        handler=handler,
        always_allowed=True,
    )


def _one_call_then_stop(**kwargs) -> _FakeProvider:
    return _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(ToolCallRequest(id="c1", name="fat", arguments={}),),
                    finish_reason="tool_calls",
                ),
            ),
            ([], Completion(content="Done.", finish_reason="stop")),
        ],
        **kwargs,
    )


async def _collect_emit():
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    return events, emit


@pytest.mark.asyncio
async def test_the_stored_result_stays_whole_while_the_transcript_shrinks():
    events, emit = await _collect_emit()
    agent = _agent()
    messages: list[dict] = [{"role": "user", "content": "go"}]

    await run_agent_loop(
        provider=_one_call_then_stop(),
        agent=agent,
        model="gpt-5-mini",
        messages=messages,
        specs=[_fat_spec(9_000)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
        tool_result_budget=500,
    )

    emitted = next(e for e in events if e["type"] == "tool_result")
    assert len(emitted["result"]["body"]) == 9_000
    # The handle the elision points at travels with the stored copy.
    assert emitted["call_id"] == "c1"

    tool_message = next(m for m in messages if m.get("role") == "tool")
    assert len(tool_message["content"]) <= 500
    assert "elided" in tool_message["content"]


@pytest.mark.asyncio
async def test_a_result_inside_the_budget_reaches_the_model_untouched():
    events, emit = await _collect_emit()
    agent = _agent()
    messages: list[dict] = [{"role": "user", "content": "go"}]

    await run_agent_loop(
        provider=_one_call_then_stop(),
        agent=agent,
        model="gpt-5-mini",
        messages=messages,
        specs=[_fat_spec(10)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
        tool_result_budget=500,
    )

    tool_message = next(m for m in messages if m.get("role") == "tool")
    assert "elided" not in tool_message["content"]
    assert "xxxxxxxxxx" in tool_message["content"]


def _history(turns: int) -> list[dict]:
    """A run already several tool round-trips deep, each call paired with its result."""
    messages: list[dict] = [
        {"role": "system", "content": "You are an operator."},
        {"role": "user", "content": "go"},
    ]
    for n in range(turns):
        messages.append(
            {
                "role": "assistant",
                "content": f"step {n}",
                "tool_calls": [{"id": f"h{n}", "type": "function", "function": {"name": "fat", "arguments": "{}"}}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"h{n}", "content": "x" * 1_000})
    return messages


@pytest.mark.asyncio
async def test_an_over_budget_transcript_folds_before_the_next_turn():
    events, emit = await _collect_emit()
    agent = _agent()
    messages = _history(6)

    await run_agent_loop(
        provider=_one_call_then_stop(summary="Earlier: called fat once."),
        agent=agent,
        model="gpt-5-mini",
        messages=messages,
        specs=[_fat_spec(10)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
        tool_result_budget=50_000,
        transcript_budget=2_000,
        keep_recent=2,
    )

    compaction = next(e for e in events if e["type"] == "compaction")
    assert compaction["after_chars"] < compaction["before_chars"]
    assert "Earlier: called fat once." in compaction["summary"]
    # The fold is applied to the caller's list, which is what the park path persists.
    assert any(m.get("role") == "system" and "Summary of" in (m.get("content") or "") for m in messages)


@pytest.mark.asyncio
async def test_a_failed_summarization_leaves_the_run_alone():
    """An optimization must never be the thing that fails a run."""
    events, emit = await _collect_emit()
    agent = _agent()
    messages = _history(6)

    result = await run_agent_loop(
        provider=_one_call_then_stop(summary_error=RuntimeError("summarizer down")),
        agent=agent,
        model="gpt-5-mini",
        messages=messages,
        specs=[_fat_spec(10)],
        ctx=_ctx(agent),
        emit=emit,
        max_iterations=4,
        tool_result_budget=50_000,
        transcript_budget=2_000,
        keep_recent=2,
    )

    assert result.final_content == "Done."
    assert not [e for e in events if e["type"] == "compaction"]
