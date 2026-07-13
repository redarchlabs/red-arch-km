"""Unit tests for the web_research tool (Gemini Google Search grounding)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.llm.provider import Completion, LLMError, LLMProvider
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import Category, ToolContext
from api.services.agents.tools.web_research import WEB_RESEARCH, _web_research

pytestmark = pytest.mark.unit

WR = "api.services.agents.tools.web_research"


def _agent(kind: str, **grants) -> Agent:
    return Agent(name="a", provider="openai", model="gpt-5-mini", kind=kind, grants=grants)


def _ctx() -> ToolContext:
    return ToolContext(
        session=None,
        org_id=uuid.uuid4(),
        settings=SimpleNamespace(agent_web_research_model="gemini/gemini-2.5-flash"),
        agent=_agent("operator", tools=["web_research"]),
    )


async def test_success_returns_answer_and_sources():
    completion = Completion(
        content="EV battery prices fell 12% this year.",
        sources=({"title": "Reuters", "url": "https://reuters.com/x", "snippet": ""},),
    )
    with (
        patch(f"{WR}.resolve_provider_key", AsyncMock(return_value="gk")),
        patch.object(LLMProvider, "complete", AsyncMock(return_value=completion)) as m,
    ):
        out = await _web_research(_ctx(), {"query": "EV battery news"})
    assert out["answer"].startswith("EV battery")
    assert out["sources"][0]["url"] == "https://reuters.com/x"
    assert out["grounded"] is True
    # Grounding tool passed alone (no function tools), on the configured model.
    _, kwargs = m.call_args
    assert kwargs["model"] == "gemini/gemini-2.5-flash"
    assert kwargs["tools"] == [{"googleSearch": {}}]


async def test_requires_query():
    out = await _web_research(_ctx(), {"query": "  "})
    assert out["error"] == "query is required"


async def test_missing_key_errors():
    with patch(f"{WR}.resolve_provider_key", AsyncMock(return_value=None)):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "Gemini API key" in out["error"]


async def test_quota_error_is_friendly():
    with (
        patch(f"{WR}.resolve_provider_key", AsyncMock(return_value="gk")),
        patch.object(LLMProvider, "complete", AsyncMock(side_effect=LLMError("429 RESOURCE_EXHAUSTED"))),
    ):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "quota" in out["error"].lower()


async def test_generic_error_surfaced():
    with (
        patch(f"{WR}.resolve_provider_key", AsyncMock(return_value="gk")),
        patch.object(LLMProvider, "complete", AsyncMock(side_effect=LLMError("boom"))),
    ):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "web research failed" in out["error"]


def test_registered_in_base_set():
    assert "web_research" in {s.name for s in base_tool_specs()}


def test_authority_read_only_operator_only():
    assert WEB_RESEARCH.category == Category.EXECUTE
    assert WEB_RESEARCH.side_effecting is False
    granted = _agent("operator", tools=["web_research"])
    # Read-only → runs free even under high-touch.
    assert decide(granted, WEB_RESEARCH, autonomy="high_touch").decision is Decision.ALLOW
    # Operator without the grant → denied.
    assert decide(_agent("operator"), WEB_RESEARCH).decision is Decision.DENY
    # Non-operators are kind-gated out of EXECUTE even if granted.
    for kind in ("advisory", "coordinator"):
        assert decide(_agent(kind, tools=["web_research"]), WEB_RESEARCH).decision is Decision.DENY
