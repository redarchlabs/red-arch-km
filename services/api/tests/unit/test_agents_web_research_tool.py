"""Unit tests for the web_research tool — Anthropic search+fetch, Gemini grounding."""

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
        settings=SimpleNamespace(
            agent_web_research_model="gemini/gemini-2.5-flash",
            agent_web_search_model="claude-opus-5",
        ),
        agent=_agent("operator", tools=["web_research"]),
    )


def _keys(*, anthropic: str | None = None, gemini: str | None = None) -> AsyncMock:
    """Stand in for ``resolve_provider_key``, which is asked per provider.

    Both backends are consulted in a fixed order, so a mock with one return value
    would silently answer for whichever is asked first — the Gemini tests would
    quietly exercise the Anthropic path.
    """
    return AsyncMock(side_effect=lambda _s, _o, provider, _set: {"anthropic": anthropic, "gemini": gemini}[provider])


async def test_success_returns_answer_and_sources():
    completion = Completion(
        content="EV battery prices fell 12% this year.",
        sources=({"title": "Reuters", "url": "https://reuters.com/x", "snippet": ""},),
    )
    with (
        patch(f"{WR}.resolve_provider_key", _keys(gemini="gk")),
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


async def test_missing_key_names_both_backends():
    # A message naming only one vendor sends whoever reads it to sign up for that
    # one when they may already have the other.
    with patch(f"{WR}.resolve_provider_key", _keys()):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "ANTHROPIC_API_KEY" in out["error"] and "GEMINI_API_KEY" in out["error"]


async def test_quota_error_is_friendly():
    with (
        patch(f"{WR}.resolve_provider_key", _keys(gemini="gk")),
        patch.object(LLMProvider, "complete", AsyncMock(side_effect=LLMError("429 RESOURCE_EXHAUSTED"))),
    ):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "quota" in out["error"].lower()


async def test_generic_error_surfaced():
    with (
        patch(f"{WR}.resolve_provider_key", _keys(gemini="gk")),
        patch.object(LLMProvider, "complete", AsyncMock(side_effect=LLMError("boom"))),
    ):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "web research failed" in out["error"]


# --- Anthropic backend ------------------------------------------------------
#
# Preferred when a key resolves, because its server tools include web_fetch: it can
# open a URL the question names, which search-grounding cannot. "Audit this page" is
# most of what anyone asks a researcher for.


def _block(**fields):
    return SimpleNamespace(**fields)


def _reply(content, stop_reason="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def _anthropic(*replies):
    """Patch the SDK client so `messages.create` returns each reply in turn."""
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=list(replies))))
    return patch("anthropic.AsyncAnthropic", lambda **_kw: client), client


async def test_anthropic_is_preferred_when_its_key_exists():
    reply = _reply([_block(type="text", text="The page loads in 1.2s.")])
    ctor, client = _anthropic(reply)
    with patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak", gemini="gk")), ctor:
        out = await _web_research(_ctx(), {"query": "audit https://example.org"})
    assert out["answer"] == "The page loads in 1.2s."
    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "claude-opus-5"
    # Both server tools offered: search finds pages, fetch opens the named one.
    assert [t["name"] for t in kwargs["tools"]] == ["web_search", "web_fetch"]


async def test_it_cites_what_it_read():
    reply = _reply(
        [
            _block(type="text", text="Three H1s on the homepage."),
            _block(
                type="web_search_tool_result",
                content=[_block(url="https://example.org/a", title="A")],
            ),
            _block(type="web_fetch_tool_result", content=[_block(url="https://example.org/", title=None)]),
        ]
    )
    ctor, _ = _anthropic(reply)
    with patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak")), ctor:
        out = await _web_research(_ctx(), {"query": "audit https://example.org"})
    assert [s["url"] for s in out["sources"]] == ["https://example.org/a", "https://example.org/"]
    # A result with no title still cites — falling back to the URL.
    assert out["sources"][1]["title"] == "https://example.org/"
    assert out["grounded"] is True


async def test_the_same_page_twice_is_one_source():
    reply = _reply(
        [
            _block(type="text", text="ok"),
            _block(type="web_search_tool_result", content=[_block(url="https://example.org/", title="Home")]),
            _block(type="web_fetch_tool_result", content=[_block(url="https://example.org/", title="Home")]),
        ]
    )
    ctor, _ = _anthropic(reply)
    with patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak")), ctor:
        out = await _web_research(_ctx(), {"query": "x"})
    assert len(out["sources"]) == 1


async def test_a_paused_turn_is_resumed():
    # A server-tool turn that hits the API's iteration cap comes back as pause_turn;
    # returning it as the answer would silently truncate the research.
    paused = _reply([_block(type="text", text="Searching…")], stop_reason="pause_turn")
    done = _reply([_block(type="text", text="Found it.")])
    ctor, client = _anthropic(paused, done)
    with patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak")), ctor:
        out = await _web_research(_ctx(), {"query": "x"})
    assert client.messages.create.await_count == 2
    assert "Found it." in out["answer"]


async def test_a_tool_error_is_not_read_as_a_list():
    # On failure the result block's `content` is a single error object, not a list —
    # same field, different shape, HTTP 200 either way.
    reply = _reply([_block(type="web_search_tool_result", content=_block(error_code="max_uses_exceeded"))])
    ctor, _ = _anthropic(reply)
    with patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak")), ctor:
        out = await _web_research(_ctx(), {"query": "x"})
    assert "max_uses_exceeded" in out["error"]


async def test_a_refusal_is_reported_not_returned_as_empty():
    reply = _reply([], stop_reason="refusal")
    ctor, _ = _anthropic(reply)
    with patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak")), ctor:
        out = await _web_research(_ctx(), {"query": "x"})
    assert "declined" in out["error"]


async def test_an_api_error_is_surfaced_not_raised():
    from anthropic import AnthropicError

    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=AnthropicError("connection reset"))))
    with (
        patch(f"{WR}.resolve_provider_key", _keys(anthropic="ak")),
        patch("anthropic.AsyncAnthropic", lambda **_kw: client),
    ):
        out = await _web_research(_ctx(), {"query": "x"})
    assert "web research failed" in out["error"]


def test_registered_in_base_set():
    assert "web_research" in {s.name for s in base_tool_specs()}


def test_authority_read_only_and_grant_gated():
    assert WEB_RESEARCH.category == Category.READ
    assert WEB_RESEARCH.side_effecting is False
    granted = _agent("operator", tools=["web_research"])
    # Read-only → runs free even under high-touch.
    assert decide(granted, WEB_RESEARCH, autonomy="high_touch").decision is Decision.ALLOW
    # Read is not free: without the grant it is still denied.
    assert decide(_agent("operator"), WEB_RESEARCH).decision is Decision.DENY


def test_an_advisory_researcher_may_read_the_web():
    # It was EXECUTE, which the kind-gate reads as operator-only — so a
    # research-analyst handed "audit this website" could not open a single page,
    # asked for permission it already had, and marked the order blocked.
    for kind in ("advisory", "coordinator", "operator"):
        assert decide(_agent(kind, tools=["web_research"]), WEB_RESEARCH).decision is Decision.ALLOW
