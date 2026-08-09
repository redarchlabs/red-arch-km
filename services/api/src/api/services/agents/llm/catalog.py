"""Provider + model catalog for the multi-provider agent org.

Drives the UI provider/model picker and maps a LiteLLM model id back to its
provider (so the runtime can resolve the right API key). Model ids are in
LiteLLM format: ``"<provider>/<model>"`` for Anthropic/Gemini, and bare model
names for OpenAI (LiteLLM's default provider). Keep these in sync with the
provider SDKs; they are the strings passed to ``litellm.acompletion``.

What ships here is the official vendor APIs plus, through the OpenAI shape, any
self-hosted server — which is the whole surface an open deployment should need.
A deployment that reaches a model over some other transport registers it with
:func:`register_provider` instead of forking this file; see
:mod:`api.services.agents.llm.plugins` for how such a module is loaded. Registered
providers are indistinguishable from built-ins to the agent roster, the admin
picker and the org credential store — the only difference is where their code
lives.

Deliberately free of imports beyond the standard library: this is the leaf that
the routing, caching and reasoning modules all build on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelDef:
    id: str  # full LiteLLM model id, e.g. "anthropic/claude-sonnet-5"
    label: str
    # Can this model look at an image? Declared rather than guessed: a model that
    # cannot gets an attachment as text, which is a working message. Sending an
    # image_url part to one that cannot is a provider error mid-run.
    vision: bool = False


@dataclass(frozen=True, slots=True)
class ProviderDef:
    name: str  # canonical provider key: anthropic | openai | gemini
    label: str
    models: tuple[ModelDef, ...]
    key_env: str  # the central-key env var (for admin docs / settings hints)


PROVIDERS: tuple[ProviderDef, ...] = (
    ProviderDef(
        "anthropic",
        "Anthropic (Claude)",
        (
            ModelDef("anthropic/claude-opus-4-8", "Claude Opus 4.8", vision=True),
            ModelDef("anthropic/claude-sonnet-5", "Claude Sonnet 5", vision=True),
            ModelDef("anthropic/claude-haiku-4-5-20251001", "Claude Haiku 4.5", vision=True),
        ),
        "ANTHROPIC_API_KEY",
    ),
    ProviderDef(
        "openai",
        "OpenAI (GPT)",
        (
            ModelDef("gpt-5", "GPT-5", vision=True),
            ModelDef("gpt-5-mini", "GPT-5 mini", vision=True),
            ModelDef("gpt-5-nano", "GPT-5 nano", vision=True),
        ),
        "OPENAI_API_KEY",
    ),
    ProviderDef(
        "gemini",
        "Google (Gemini)",
        (
            ModelDef("gemini/gemini-2.5-pro", "Gemini 2.5 Pro", vision=True),
            ModelDef("gemini/gemini-2.5-flash", "Gemini 2.5 Flash", vision=True),
        ),
        "GEMINI_API_KEY",
    ),
)

VALID_PROVIDERS: frozenset[str] = frozenset(p.name for p in PROVIDERS)

# LiteLLM prefixes that unambiguously denote a provider. Anything without a
# recognized prefix is treated as OpenAI (LiteLLM's default).
_KNOWN_PREFIXES: frozenset[str] = frozenset({"anthropic", "openai", "gemini"})

# Builds the transport for one of a plugin's models: (settings, model, api_key).
# Typed loosely on purpose — a plugin's transport only has to satisfy the shape
# the runtime uses (`stream`/`complete`), not inherit from LLMProvider, and this
# module must not import the provider it would otherwise name here.
TransportFactory = Callable[[Any, str, "str | None"], Any]

# provider name -> (definition, factory). Process-global and written only at
# startup, by import of the modules named in LLM_PROVIDER_PLUGINS.
_REGISTERED: dict[str, tuple[ProviderDef, TransportFactory]] = {}


def register_provider(definition: ProviderDef, factory: TransportFactory) -> None:
    """Add an out-of-tree provider to the catalog.

    Refuses to shadow a built-in: quietly rebinding "anthropic" would reroute
    every Claude agent in the deployment, and a name collision is far more likely
    to be a mistake than an intent.
    """
    if definition.name in VALID_PROVIDERS:
        raise ValueError(f"'{definition.name}' is a built-in provider and cannot be replaced")
    _REGISTERED[definition.name] = (definition, factory)


def reset_registry() -> None:
    """Drop every registered provider. For tests; the registry is process-global."""
    _REGISTERED.clear()


def providers() -> tuple[ProviderDef, ...]:
    """Every provider this process offers: the built-ins, then any registered."""
    return PROVIDERS + tuple(d for d, _ in _REGISTERED.values())


def valid_providers() -> frozenset[str]:
    """The provider names an agent or an org credential may name."""
    return VALID_PROVIDERS | frozenset(_REGISTERED)


def provider_factory(provider: str) -> TransportFactory | None:
    """The transport factory for a registered provider, or None for a built-in."""
    entry = _REGISTERED.get(provider)
    return entry[1] if entry else None


def provider_for_model(model: str) -> str:
    """Return the canonical provider key for a LiteLLM model id.

    ``"anthropic/claude-…" -> "anthropic"``; a bare ``"gpt-5"`` -> ``"openai"``.
    A registered provider claims its own prefix; everything unrecognized stays
    OpenAI, which is what makes an OpenAI-shaped local server work unconfigured.
    """
    prefix, sep, _rest = model.partition("/")
    if sep and (prefix in _KNOWN_PREFIXES or prefix in _REGISTERED):
        return prefix
    return "openai"


def bare_model(model: str) -> str:
    """Strip a known provider prefix: ``openai/gpt-5-mini`` -> ``gpt-5-mini``.

    Anything else is returned unchanged, so an unrecognized prefix is never
    silently eaten. Lives here rather than beside its callers because both the
    endpoint router and the reasoning-param rules key off the bare id, and this
    module is the one with no dependencies of its own.
    """
    prefix, sep, rest = model.partition("/")
    return rest if sep and (prefix in _KNOWN_PREFIXES or prefix in _REGISTERED) else model


def model_supports_vision(model: str) -> bool:
    """Whether ``model`` can be shown an image.

    Compared on the BARE id, because the two halves of the system spell a model
    differently: an agent stores ``openai/gpt-5-mini`` (LiteLLM needs the prefix)
    while this table lists OpenAI models unprefixed. An exact match therefore said
    False for every OpenAI agent in the roster, and every pasted image was quietly
    downgraded to text — a failure that looks exactly like a model choosing not to
    mention the picture.

    Unknown models still answer False. A locally served model (``qwen3-30b`` and
    anything else reached through an OpenAI-shaped endpoint) is not in this table,
    and guessing yes would send an image_url part to something that errors on it
    mid-run. Guessing no costs a picture; guessing yes costs the turn.
    """
    wanted = bare_model(model)
    for provider in providers():
        for candidate in provider.models:
            if candidate.id == model or bare_model(candidate.id) == wanted:
                return candidate.vision
    return False
