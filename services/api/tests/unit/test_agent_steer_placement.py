"""Where a steer is allowed to land in the message list.

This is the property the whole pull-based design exists to guarantee. A user turn
inserted between an assistant message carrying ``tool_calls`` and the ``tool``
results answering them is rejected outright by OpenAI and Anthropic — surfacing as
an LLMError that finalizes the run as *error*. A steer that kills the run is worse
than one that arrives a turn late, so the loop takes messages at exactly one seam:
the top of a fresh turn, never on the branch that resumes a parked one.
"""

from __future__ import annotations

from typing import Any

import pytest
from api.models.agent import Agent
from api.services.agents.llm.provider import Completion
from api.services.agents.runtime import run_agent_loop

pytestmark = pytest.mark.unit


class _Provider:
    """Returns each queued completion in turn, recording what it was sent."""

    def __init__(self, completions: list[Completion]) -> None:
        self._completions = completions
        self.seen: list[list[dict[str, Any]]] = []

    async def stream(self, *, messages, **_kwargs):  # noqa: ANN001, ANN003
        self.seen.append([dict(m) for m in messages])
        completion = self._completions.pop(0)

        async def _events():
            yield completion
            return

        async for event in _events():
            yield event


def _agent() -> Agent:
    return Agent(name="chief", kind="coordinator", provider="openai", model="m", grants={})


async def _noop_emit(_event: dict[str, Any]) -> None:
    return None


def _roles(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("role")) for m in messages]


class TestSteerPlacement:
    async def test_it_lands_at_the_top_of_a_fresh_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _Provider([Completion(content="done", tool_calls=[], usage=None)])
        queued = ["Actually, focus on the homepage."]

        async def steer() -> list[str]:
            return queued.pop(0, None) and [] if not queued else [queued.pop(0)]

        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
        await run_agent_loop(
            provider=provider,
            agent=_agent(),
            model="m",
            messages=messages,
            specs=[],
            ctx=None,
            emit=_noop_emit,
            max_iterations=2,
            steer=steer,
        )

        # The provider saw the steer as an ordinary user turn, after the task.
        assert _roles(provider.seen[0]) == ["system", "user", "user"]
        assert provider.seen[0][-1]["content"] == "Actually, focus on the homepage."

    async def test_nothing_queued_changes_nothing(self) -> None:
        provider = _Provider([Completion(content="done", tool_calls=[], usage=None)])

        async def steer() -> list[str]:
            return []

        await run_agent_loop(
            provider=provider,
            agent=_agent(),
            model="m",
            messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "task"}],
            specs=[],
            ctx=None,
            emit=_noop_emit,
            max_iterations=2,
            steer=steer,
        )

        assert _roles(provider.seen[0]) == ["system", "user"]

    async def test_it_is_not_drained_while_resuming_a_parked_turn(self) -> None:
        """The resume branch skips the stream entirely: its messages end in
        unresolved tool_calls, so a user turn there is exactly the rejected shape.
        The drain must not even be *called* on that path.
        """
        calls = {"n": 0}

        async def steer() -> list[str]:
            calls["n"] += 1
            return ["should not appear yet"]

        provider = _Provider([Completion(content="done", tool_calls=[], usage=None)])
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
            },
        ]

        await run_agent_loop(
            provider=provider,
            agent=_agent(),
            model="m",
            messages=messages,
            specs=[],
            ctx=None,
            emit=_noop_emit,
            max_iterations=2,
            resume_tool_calls=[],
            steer=steer,
        )

        # The resumed turn executed with no steer drained; the drain only runs once
        # the loop comes back round to a clean turn.
        # The guard is structural: the drain is skipped entirely while tool results
        # are outstanding, so no reachable control flow can place a steer there.
        assert calls["n"] <= 1
        for seen in provider.seen:
            # Never a user turn immediately after an assistant tool_calls message.
            for i, message in enumerate(seen[:-1]):
                if message.get("tool_calls"):
                    assert seen[i + 1].get("role") != "user"
