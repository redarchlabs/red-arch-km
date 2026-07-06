"""Unit tests for the agentic query router (agent mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from brain_api.routers.agent import AgentAskRequest, agent_ask, agent_ask_stream
from brain_sdk.facts.agent import AgentResult
from brain_sdk.facts.gaps import InMemoryGapLog


class _FakeFactStore:
    """Records gaps into an in-memory log (router calls ``record_gap``)."""

    def __init__(self) -> None:
        self.log = InMemoryGapLog()

    def record_gap(self, gap):  # type: ignore[no-untyped-def]
        return self.log.record(gap)


def _stores() -> MagicMock:
    stores = MagicMock()
    stores.fact_store = _FakeFactStore()
    return stores


def _body() -> AgentAskRequest:
    return AgentAskRequest(tenant_id="t1", query="Where is Acme HQ?", access_keys=[1], tags=[])


@pytest.mark.asyncio
async def test_agent_ask_returns_answer_and_grounding() -> None:
    agent = MagicMock()
    agent.run.return_value = AgentResult(
        answer="Acme is in Paris [E1].",
        citations=["E1"],
        evidence=[{"id": "E1", "tool": "claim_query", "result": [{"x": 1}]}],
        iterations=2,
        unsupported_citations=[],
    )
    stores = _stores()

    result = await agent_ask(_body(), agent=agent, stores=stores, _api_key="x")

    assert result["answer"] == "Acme is in Paris [E1]."
    assert result["citations"] == ["E1"]
    assert result["unsupported_citations"] == []
    assert result["iterations"] == 2
    # tenant + access_keys are passed via the trusted context, not model input.
    ctx = agent.run.call_args.args[1]
    assert ctx.tenant_id == "t1"
    assert ctx.access_keys == (1,)
    # Facts were found -> no gap recorded.
    assert stores.fact_store.log.list_open("t1") == []


@pytest.mark.asyncio
async def test_agent_ask_records_gap_when_facts_empty() -> None:
    agent = MagicMock()
    agent.run.return_value = AgentResult(
        answer="No information available.",
        citations=[],
        evidence=[{"id": "E1", "tool": "claim_query", "result": []}],
        iterations=1,
    )
    stores = _stores()

    await agent_ask(_body(), agent=agent, stores=stores, _api_key="x")

    gaps = stores.fact_store.log.list_open("t1")
    assert len(gaps) == 1
    assert gaps[0].question == "Where is Acme HQ?"
    assert gaps[0].fact_rows == 0


@pytest.mark.asyncio
async def test_agent_ask_stream_emits_sse_frames_and_captures_gap() -> None:
    agent = MagicMock()
    agent.stream.return_value = iter(
        [
            {"type": "thought", "content": "look it up"},
            {"type": "tool_call", "tool": "claim_query", "args": {}},
            {"type": "tool_result", "tool": "claim_query", "records": []},
            {"type": "final", "answer": "No info", "citations": [], "unsupported_citations": []},
        ]
    )
    stores = _stores()

    response = await agent_ask_stream(_body(), agent=agent, stores=stores, _api_key="x")

    frames = [chunk async for chunk in response.body_iterator]
    body = "".join(frames)
    assert body.startswith("data: ")
    assert '"type": "thought"' in body
    assert '"type": "final"' in body
    assert body.count("data: ") == 4
    # The empty claim_query in the trace should have been captured as a gap.
    gaps = stores.fact_store.log.list_open("t1")
    assert len(gaps) == 1
    assert gaps[0].question == "Where is Acme HQ?"


@pytest.mark.asyncio
async def test_agent_ask_stream_no_gap_when_facts_present() -> None:
    agent = MagicMock()
    agent.stream.return_value = iter(
        [
            {"type": "tool_result", "tool": "claim_query", "records": [{"x": 1}]},
            {"type": "final", "answer": "Paris [E1]", "citations": ["E1"]},
        ]
    )
    stores = _stores()

    response = await agent_ask_stream(_body(), agent=agent, stores=stores, _api_key="x")
    [_ async for _ in response.body_iterator]  # drain

    assert stores.fact_store.log.list_open("t1") == []
