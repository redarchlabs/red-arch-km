"""Construct a provider-agnostic LLM client from configuration.

OpenAI is the default provider. Other providers are imported lazily by their
own modules, so only the SDKs you actually use need to be installed.
"""

from __future__ import annotations

from brain_sdk.llm.protocol import LLMClient

DEFAULT_PROVIDER = "openai"


def make_llm_client(
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> LLMClient:
    """Build an :class:`LLMClient` for ``provider``.

    Supported: ``openai`` (default), ``anthropic``, ``gemini``.
    """
    normalized = provider.strip().lower()
    if normalized in ("openai", "azure-openai"):
        from brain_sdk.llm.openai_client import OpenAILLMClient

        return OpenAILLMClient(api_key=api_key, model=model, base_url=base_url)
    if normalized in ("anthropic", "claude"):
        from brain_sdk.llm.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(api_key=api_key, model=model, base_url=base_url)
    if normalized in ("gemini", "google"):
        from brain_sdk.llm.gemini_client import GeminiLLMClient

        return GeminiLLMClient(api_key=api_key, model=model)
    msg = f"unknown LLM provider: {provider!r} (supported: openai, anthropic, gemini)"
    raise ValueError(msg)
