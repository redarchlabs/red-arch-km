"""Chat-completion params that OpenAI's reasoning models spell differently.

The gpt-5 family and the o-series reject two things every other chat model takes:

* ``max_tokens`` — 400s with *"Use 'max_completion_tokens' instead"*.
* any ``temperature`` but their own default — 400s with *"Only the default (1)
  value is supported"*.

Both are provider facts rather than application policy, and both are needed by
more than one service: ``api`` applies them on the agent, chat and workflow
paths, and ``brain_api`` needs them on the RAG answer path. They live in
``shared_config`` because that is what both already depend on — a second copy is
how ``brain_api`` came to send ``max_tokens`` to a gpt-5.6 model and fail every
RAG answer with a bare "Streaming failed" after the sources had already rendered.

Keyed on the model id, because one deployment mixes OpenAI, local llama.cpp and
other OpenAI-compatible servers, and a parameter meant for one must not reach
another. llama.cpp accepts BOTH spellings, so keying on the reasoning family
changes the wire format only where it has to.
"""

from __future__ import annotations

from typing import Any

# Provider prefixes an id may carry (``openai/gpt-5-mini``). Mirrors
# ``api.services.agents.llm.catalog._KNOWN_PREFIXES``; a prefix that is not one of
# these is left alone rather than silently eaten, so an unrecognized id still
# matches on its own text.
_KNOWN_PREFIXES = frozenset({"anthropic", "openai", "gemini"})

# gpt-5-chat-* is the family's non-reasoning sibling and takes the ordinary
# params, so it has to be excluded before the "gpt-5" prefix matches it.
_NON_REASONING_PREFIXES = ("gpt-5-chat",)
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def bare_model(model: str) -> str:
    """``openai/gpt-5-mini`` -> ``gpt-5-mini``; anything else unchanged."""
    prefix, sep, rest = model.partition("/")
    return rest if sep and prefix in _KNOWN_PREFIXES else model


def is_reasoning_model(model: str) -> bool:
    """Whether ``model`` is one of the families with the two quirks above."""
    name = bare_model(model).lower()
    if name.startswith(_NON_REASONING_PREFIXES):
        return False
    return name.startswith(_REASONING_PREFIXES)


def temperature_for(model: str, requested: float | None) -> float | None:
    """``requested``, or ``None`` for a reasoning model — which takes only its default.

    ``None`` means *omit the parameter*, not *send zero*: sending the default
    explicitly is still a 400 on some of these models."""
    return None if is_reasoning_model(model) else requested


def token_limit_kwargs(model: str, limit: int | None) -> dict[str, int]:
    """The output-length cap under whichever name ``model`` accepts.

    Spreadable straight into an SDK call; empty when no cap was asked for."""
    if limit is None:
        return {}
    key = "max_completion_tokens" if is_reasoning_model(model) else "max_tokens"
    return {key: limit}


def chat_kwargs(model: str, *, max_tokens: int | None = None, temperature: float | None = None) -> dict[str, Any]:
    """Every quirk-sensitive param at once, ready to spread into ``create()``.

    One call site per request is the point: the failure this prevents is fixing
    ``max_tokens`` and then discovering ``temperature`` is refused too."""
    kwargs: dict[str, Any] = token_limit_kwargs(model, max_tokens)
    temp = temperature_for(model, temperature)
    if temp is not None:
        kwargs["temperature"] = temp
    return kwargs
