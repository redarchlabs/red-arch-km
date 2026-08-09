"""Call params that only OpenAI's reasoning models take — and only they.

A reasoning model (the gpt-5 family, the o-series) thinks in hidden tokens before
it answers, and those tokens are billed as output. Absent ``reasoning_effort`` the
API picks *medium*, which on a tool-calling loop is a cost paid on every step of
every run — the difference between a small model being cheap and it quietly not
being. So the agent path always sends an effort rather than accepting the API
default, and lets an agent override it per row via ``params.reasoning_effort``.

The same models refuse a ``temperature`` other than their default. That makes the
sampling knob every other agent has a latent 400 the moment an agent is pointed at
gpt-5, so it is dropped here rather than left to fail at the provider.

Both rules are decided per model id, because one roster mixes OpenAI, Anthropic,
Gemini and local Qwen agents and a parameter meant for one must not reach another.
"""

from __future__ import annotations

from api.services.agents.llm.catalog import bare_model

# The tiers the API accepts, cheapest first.
EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high")

# What an agent gets when it names no effort of its own. Not "medium" (the API's
# own default) because that is the spend this module exists to bound, and not
# "minimal" because suppressing the reasoning is most of why a reasoning model was
# chosen for a tool loop in the first place.
DEFAULT_EFFORT = "low"

# gpt-5-chat-* is the non-reasoning sibling of the family and rejects the param,
# so it has to be excluded before the "gpt-5" prefix matches it.
_NON_REASONING_PREFIXES = ("gpt-5-chat",)
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# The o-series predates the "minimal" tier and 400s on it; "low" is its floor.
_O_SERIES_PREFIXES = ("o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    """Whether ``model`` spends hidden reasoning tokens and takes an effort tier."""
    name = bare_model(model).lower()
    if name.startswith(_NON_REASONING_PREFIXES):
        return False
    return name.startswith(_REASONING_PREFIXES)


def reasoning_effort_for(model: str, requested: str | None = None) -> str | None:
    """The ``reasoning_effort`` to send for ``model``, or ``None`` to send none.

    ``requested`` is whatever the agent row carries, which is free-form JSON: an
    unusable value falls back to :data:`DEFAULT_EFFORT` rather than failing the
    run, since a typo in config should not take an agent offline.
    """
    if not is_reasoning_model(model):
        return None
    effort = requested if requested in EFFORTS else DEFAULT_EFFORT
    if effort == "minimal" and bare_model(model).lower().startswith(_O_SERIES_PREFIXES):
        return "low"
    return effort


def temperature_for(model: str, requested: float | None) -> float | None:
    """``requested``, or ``None`` for a reasoning model — which takes only its default."""
    return None if is_reasoning_model(model) else requested


def reasoning_kwargs(model: str, requested: str | None = None) -> dict[str, str]:
    """``{"reasoning_effort": tier}``, or ``{}`` — spreadable into a raw SDK call.

    The chat, RAG and workflow paths call the OpenAI SDK directly rather than
    through :class:`~api.services.agents.llm.provider.LLMProvider`, and they run on
    whatever model an org is pinned to. They share these rules so that pinning an
    org to gpt-5-mini does not quietly buy every one of them medium effort.
    """
    effort = reasoning_effort_for(model, requested)
    return {"reasoning_effort": effort} if effort else {}
