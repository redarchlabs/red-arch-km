"""Reasoning-model call params on the agent path.

An OpenAI reasoning model (gpt-5 family, o-series) spends hidden reasoning tokens
before it answers, billed as output, and defaults to *medium* effort when the
parameter is absent. On a tool-calling loop that is a cost paid on every step of
every run — which is how a "cheap" model stops being cheap. It also refuses a
``temperature``, so an agent carrying one in ``params`` would 400 the moment it
was pointed at gpt-5.

Both rules are per-model: the roster mixes OpenAI, Anthropic, Gemini and local
Qwen agents, and a parameter meant for one must never be sent to another.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api.services.agents.llm import provider as prov
from api.services.agents.llm.provider import LLMProvider
from api.services.agents.llm.reasoning import (
    DEFAULT_EFFORT,
    is_reasoning_model,
    reasoning_effort_for,
    temperature_for,
)

pytestmark = pytest.mark.unit


class TestIsReasoningModel:
    def test_the_gpt_5_family_reasons(self) -> None:
        assert is_reasoning_model("gpt-5")
        assert is_reasoning_model("gpt-5-mini")
        assert is_reasoning_model("gpt-5-nano")
        # The agent path names OpenAI models with a LiteLLM prefix.
        assert is_reasoning_model("openai/gpt-5-mini")

    def test_the_o_series_reasons(self) -> None:
        assert is_reasoning_model("o3-mini")
        assert is_reasoning_model("openai/o4-mini")

    def test_gpt_5_chat_does_not(self) -> None:
        # The non-reasoning sibling: it rejects reasoning_effort outright.
        assert not is_reasoning_model("gpt-5-chat-latest")

    def test_other_providers_do_not(self) -> None:
        assert not is_reasoning_model("anthropic/claude-sonnet-5")
        assert not is_reasoning_model("gemini/gemini-2.5-flash")
        assert not is_reasoning_model("gpt-4.1-mini")
        # A local llama.cpp model served on the OpenAI shape takes no such param.
        assert not is_reasoning_model("openai/qwen3-30b")


class TestReasoningEffort:
    def test_a_reasoning_model_gets_the_default_rather_than_the_api_default(self) -> None:
        # Sending nothing would leave the model at medium on every loop step.
        assert reasoning_effort_for("openai/gpt-5-mini") == DEFAULT_EFFORT
        assert DEFAULT_EFFORT == "low"

    def test_an_explicit_request_wins(self) -> None:
        assert reasoning_effort_for("gpt-5", "high") == "high"
        assert reasoning_effort_for("gpt-5", "minimal") == "minimal"

    def test_an_unusable_request_falls_back_rather_than_failing_the_run(self) -> None:
        # Params are free-form JSON on the agent row; a typo must not 400 a run.
        assert reasoning_effort_for("gpt-5", "cheap") == DEFAULT_EFFORT
        assert reasoning_effort_for("gpt-5", "") == DEFAULT_EFFORT

    def test_the_o_series_has_no_minimal_tier(self) -> None:
        assert reasoning_effort_for("o3-mini", "minimal") == "low"
        assert reasoning_effort_for("o3-mini", "high") == "high"

    def test_the_5_6_family_has_no_minimal_tier_either(self) -> None:
        # sol/terra/luna 400 on "minimal"; "low" is their floor.
        assert reasoning_effort_for("gpt-5.6-luna", "minimal") == "low"
        assert reasoning_effort_for("openai/gpt-5.6-terra", "minimal") == "low"
        assert reasoning_effort_for("gpt-5.6-sol", "high") == "high"

    def test_the_5_6_family_still_reasons_by_default(self) -> None:
        assert is_reasoning_model("gpt-5.6-luna")
        assert reasoning_effort_for("openai/gpt-5.6-luna") == DEFAULT_EFFORT


    def test_a_non_reasoning_model_is_sent_none_even_when_asked(self) -> None:
        assert reasoning_effort_for("anthropic/claude-sonnet-5", "high") is None
        assert reasoning_effort_for("gpt-5-chat-latest", "low") is None
        assert reasoning_effort_for("openai/qwen3-30b") is None


class TestTemperature:
    def test_a_reasoning_model_is_sent_no_temperature(self) -> None:
        # It accepts only its default; sending one is a 400.
        assert temperature_for("openai/gpt-5-mini", 0.2) is None

    def test_every_other_model_keeps_the_configured_temperature(self) -> None:
        assert temperature_for("anthropic/claude-sonnet-5", 0.2) == 0.2
        assert temperature_for("gpt-4.1-mini", 0.0) == 0.0
        assert temperature_for("anthropic/claude-sonnet-5", None) is None


class TestTheWriteBoundaryRefusesABadTier:
    """The runtime falls back rather than failing a run; the admin API says no.

    A run must survive bad config, but an admin who typed a tier that does not
    exist should be told at the point of saving, not left with an agent quietly
    running at a different effort than the one they chose.
    """

    def test_a_valid_tier_is_accepted(self) -> None:
        from api.schemas.agent import AgentCreate

        agent = AgentCreate(name="planner", provider="openai", model="gpt-5-mini", params={"reasoning_effort": "high"})
        assert agent.params["reasoning_effort"] == "high"

    def test_an_invalid_tier_is_refused(self) -> None:
        from api.schemas.agent import AgentCreate, AgentUpdate
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="reasoning_effort"):
            AgentCreate(name="planner", provider="openai", model="gpt-5-mini", params={"reasoning_effort": "maximum"})
        with pytest.raises(ValidationError, match="reasoning_effort"):
            AgentUpdate(params={"reasoning_effort": "maximum"})

    def test_params_without_an_effort_are_untouched(self) -> None:
        from api.schemas.agent import AgentUpdate

        assert AgentUpdate(params={"temperature": 0.2}).params == {"temperature": 0.2}


# --- provider seam ----------------------------------------------------------


def _chunk(*, content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, delta=SimpleNamespace(content=content, tool_calls=None))],
        usage=None,
    )


class _Capturing:
    """Stands in for the lazy ``litellm`` import and records the call kwargs."""

    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def acompletion(self, **kwargs):
        self._captured.update(kwargs)

        async def gen():
            yield _chunk(content="ok", finish_reason="stop")

        return gen()


@pytest.fixture
def captured(monkeypatch) -> dict:
    seen: dict = {}
    monkeypatch.setattr(prov, "_litellm", lambda: _Capturing(seen))
    return seen


class TestProviderSendsTheRightParams:
    @pytest.mark.asyncio
    async def test_a_reasoning_model_gets_effort_and_no_temperature(self, captured) -> None:
        stream = LLMProvider().stream(
            model="openai/gpt-5-mini",
            messages=[{"role": "user", "content": "go"}],
            temperature=0.2,
            reasoning_effort="high",
        )
        [_ async for _ in stream]

        assert captured["reasoning_effort"] == "high"
        assert "temperature" not in captured

    @pytest.mark.asyncio
    async def test_a_reasoning_model_with_no_request_still_gets_a_pinned_effort(self, captured) -> None:
        stream = LLMProvider().stream(model="gpt-5-mini", messages=[{"role": "user", "content": "go"}])
        [_ async for _ in stream]

        assert captured["reasoning_effort"] == DEFAULT_EFFORT

    @pytest.mark.asyncio
    async def test_a_non_reasoning_model_gets_temperature_and_no_effort(self, captured) -> None:
        stream = LLMProvider().stream(
            model="anthropic/claude-sonnet-5",
            messages=[{"role": "user", "content": "go"}],
            temperature=0.2,
            reasoning_effort="high",
        )
        [_ async for _ in stream]

        assert captured["temperature"] == 0.2
        assert "reasoning_effort" not in captured

    @pytest.mark.asyncio
    async def test_a_5_6_agent_turn_keeps_its_effort_alongside_tools(self, captured) -> None:
        # The shape of a real agent step: tools present, an effort on the row.
        # 5.6 accepts both together (verified live), so nothing is downgraded.
        stream = LLMProvider().stream(
            model="openai/gpt-5.6-luna",
            messages=[{"role": "user", "content": "go"}],
            tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
            reasoning_effort="high",
        )
        [_ async for _ in stream]

        assert captured["tools"]
        assert captured["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_a_5_6_agent_row_asking_for_minimal_is_floored_not_failed(self, captured) -> None:
        # "minimal" is the one tier 5.6 rejects outright; an agent row carrying it
        # must not 400 the run.
        stream = LLMProvider().stream(
            model="openai/gpt-5.6-luna",
            messages=[{"role": "user", "content": "go"}],
            tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
            reasoning_effort="minimal",
        )
        [_ async for _ in stream]

        assert captured["reasoning_effort"] == "low"

    @pytest.mark.asyncio
    async def test_the_same_rules_hold_for_a_single_shot_completion(self, captured) -> None:
        await LLMProvider().complete(
            model="gpt-5-nano",
            messages=[{"role": "user", "content": "go"}],
            temperature=0.7,
        )

        assert captured["reasoning_effort"] == DEFAULT_EFFORT
        assert "temperature" not in captured
