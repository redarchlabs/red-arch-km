"""Reasoning-model call params — the two spellings the gpt-5 family insists on.

These are provider facts, verified against the live API on 2026-08-22 rather than
taken from documentation:

    max_tokens        -> 400 "Use 'max_completion_tokens' instead"
    temperature=0.3   -> 400 "Only the default (1) value is supported"
    max_completion_tokens, no temperature -> 200

Both had to be found; fixing only the first swaps one 400 for another.
"""

from __future__ import annotations

import pytest
from shared_config.model_params import (
    bare_model,
    chat_kwargs,
    is_reasoning_model,
    temperature_for,
    token_limit_kwargs,
)

REASONING = ("gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.6-luna", "gpt-5.6-sol", "o1", "o3", "o4-mini")
ORDINARY = ("gpt-4.1-mini", "gpt-4o", "qwen3-30b", "qwen3-4b-fast", "claude-opus-4", "gpt-5-chat-latest")


@pytest.mark.parametrize("model", REASONING)
def test_the_reasoning_families_are_recognised(model: str) -> None:
    assert is_reasoning_model(model)


@pytest.mark.parametrize("model", ORDINARY)
def test_everything_else_is_left_alone(model: str) -> None:
    assert not is_reasoning_model(model)


def test_the_non_reasoning_sibling_is_excluded_before_the_prefix_matches() -> None:
    """gpt-5-chat-* starts with "gpt-5" but takes the ordinary params."""
    assert not is_reasoning_model("gpt-5-chat-latest")
    assert chat_kwargs("gpt-5-chat-latest", max_tokens=10, temperature=0.3) == {
        "max_tokens": 10,
        "temperature": 0.3,
    }


@pytest.mark.parametrize("model", REASONING)
def test_a_reasoning_model_gets_max_completion_tokens(model: str) -> None:
    assert token_limit_kwargs(model, 1000) == {"max_completion_tokens": 1000}


@pytest.mark.parametrize("model", ORDINARY)
def test_everything_else_keeps_max_tokens(model: str) -> None:
    """llama.cpp accepts both spellings, so there is no reason to change the wire
    format for models that never had the problem."""
    assert token_limit_kwargs(model, 1000) == {"max_tokens": 1000}


@pytest.mark.parametrize("model", REASONING)
def test_temperature_is_omitted_for_a_reasoning_model(model: str) -> None:
    """Omitted, not defaulted — sending the default explicitly is still a 400."""
    assert temperature_for(model, 0.3) is None
    assert "temperature" not in chat_kwargs(model, max_tokens=100, temperature=0.3)


def test_temperature_survives_for_an_ordinary_model() -> None:
    """0.3 is a deliberate choice on the RAG path: lower temperature keeps the
    answer closer to the retrieved sources. Dropping it everywhere to dodge the
    400 would quietly change answer quality on every local model."""
    assert chat_kwargs("qwen3-30b", max_tokens=100, temperature=0.3)["temperature"] == 0.3


def test_no_cap_asked_for_means_no_parameter_sent() -> None:
    assert token_limit_kwargs("gpt-5.6-luna", None) == {}
    assert chat_kwargs("gpt-5.6-luna") == {}


def test_a_provider_prefix_is_stripped_before_matching() -> None:
    """The two halves of the system spell a model differently: KM2 agents carry a
    LiteLLM prefix, the raw SDK paths do not."""
    assert bare_model("openai/gpt-5-mini") == "gpt-5-mini"
    assert is_reasoning_model("openai/gpt-5-mini")


def test_an_unknown_prefix_is_not_eaten() -> None:
    """A slash is not automatically a provider prefix — a served model id may
    legitimately contain one, and swallowing it would misclassify the model."""
    assert bare_model("myorg/custom-model") == "myorg/custom-model"
    assert not is_reasoning_model("myorg/custom-model")


def test_the_exact_call_that_was_failing() -> None:
    """brain_api's RAG stream, verbatim: model gpt-5.6-luna, cap 1000, temp 0.3.
    This is the combination that 400d twice and produced a bare "Streaming
    failed" under a fully rendered source list."""
    assert chat_kwargs("gpt-5.6-luna", max_tokens=1000, temperature=0.3) == {"max_completion_tokens": 1000}
