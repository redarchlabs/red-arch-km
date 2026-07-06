"""Unit tests for the agentic query router (agent mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from brain_api.routers.agent import AgentAskRequest, agent_ask, agent_ask_stream
from brain_sdk.facts.agent import AgentResult


def _body() -> AgentAskRequest:
    return AgentAskRequest(tenant_id="t1", query="Where is Acme HQ?", access_keys=[1], tags=[])


@pytest.mark.asyncio
async def test_agent_ask_returns_answer_and_grounding() -> None:
    agent = MagicMock()
    agent.run.return_value = AgentResult(
        answer="Acme is in Paris [E1].",
        citations=["E1"],
        evidence=[{"id": "E1", "tool": "claim_query", "result": []}],
        iterations=2,
        unsupported_citations=[],
    )

    result = await agent_ask(_body(), agent=agent, _api_key="x")

    assert result["answer"] == "Acme is in Paris [E1]."
    assert result["citations"] == ["E1"]
    assert result["unsupported_citations"] == []
    assert result["iterations"] == 2
    # tenant + access_keys are passed via the trusted context, not model input.
    _, kwargs = agent.run.call_args
    ctx = agent.run.call_args.args[1]
    assert ctx.tenant_id == "t1"
    assert ctx.access_keys == (1,)


@pytest.mark.asyncio
async def test_agent_ask_stream_emits_sse_frames() -> None:
    agent = MagicMock()
    agent.stream.return_value = iter(
        [
            {"type": "thought", "content": "look it up"},
            {"type": "tool_call", "tool": "claim_query", "args": {}},
            {"type": "final", "answer": "Paris [E1]", "citations": ["E1"], "unsupported_citations": []},
        ]
    )

    response = await agent_ask_stream(_body(), agent=agent, _api_key="x")

    frames = [chunk async for chunk in response.body_iterator]
    body = "".join(frames)
    assert body.startswith("data: ")
    assert '"type": "thought"' in body
    assert '"type": "final"' in body
    assert body.count("data: ") == 3
