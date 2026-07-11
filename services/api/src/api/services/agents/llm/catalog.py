"""Provider + model catalog for the multi-provider agent org.

Drives the UI provider/model picker and maps a LiteLLM model id back to its
provider (so the runtime can resolve the right API key). Model ids are in
LiteLLM format: ``"<provider>/<model>"`` for Anthropic/Gemini, and bare model
names for OpenAI (LiteLLM's default provider). Keep these in sync with the
provider SDKs; they are the strings passed to ``litellm.acompletion``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelDef:
    id: str  # full LiteLLM model id, e.g. "anthropic/claude-sonnet-5"
    label: str


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
            ModelDef("anthropic/claude-opus-4-8", "Claude Opus 4.8"),
            ModelDef("anthropic/claude-sonnet-5", "Claude Sonnet 5"),
            ModelDef("anthropic/claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ),
        "ANTHROPIC_API_KEY",
    ),
    ProviderDef(
        "openai",
        "OpenAI (GPT)",
        (
            ModelDef("gpt-5", "GPT-5"),
            ModelDef("gpt-5-mini", "GPT-5 mini"),
            ModelDef("gpt-5-nano", "GPT-5 nano"),
        ),
        "OPENAI_API_KEY",
    ),
    ProviderDef(
        "gemini",
        "Google (Gemini)",
        (
            ModelDef("gemini/gemini-2.5-pro", "Gemini 2.5 Pro"),
            ModelDef("gemini/gemini-2.5-flash", "Gemini 2.5 Flash"),
        ),
        "GEMINI_API_KEY",
    ),
)

VALID_PROVIDERS: frozenset[str] = frozenset(p.name for p in PROVIDERS)

# LiteLLM prefixes that unambiguously denote a provider. Anything without a
# recognized prefix is treated as OpenAI (LiteLLM's default).
_KNOWN_PREFIXES: frozenset[str] = frozenset({"anthropic", "openai", "gemini"})


def provider_for_model(model: str) -> str:
    """Return the canonical provider key for a LiteLLM model id.

    ``"anthropic/claude-…" -> "anthropic"``; a bare ``"gpt-5"`` -> ``"openai"``.
    """
    prefix, sep, _rest = model.partition("/")
    if sep and prefix in _KNOWN_PREFIXES:
        return prefix
    return "openai"
