"""Build the OpenAI SDK client, optionally pointed at a self-hosted endpoint.

Mirrors ``api/services/openai_client.py`` in the API service. The two are deliberately
separate small modules rather than a shared dependency: the services are independently
deployable and each owns its own ``Settings`` class.

The OpenAI SDK speaks plain HTTP to whatever ``base_url`` it is given, so an
OpenAI-compatible server (Ollama, vLLM, llama.cpp) is a configuration change rather than
a code change. ``OPENAI_BASE_URL`` unset preserves the hosted behaviour exactly.

The wrinkle this hides: a self-hosted endpoint needs no credential, but the SDK still
requires a non-empty ``api_key``. Supplying a placeholder keeps a fully-local deployment
from failing on a key it will never use.
"""

from __future__ import annotations

from typing import Any

# Imported at module scope (unlike the API service's copy, which keeps ``openai`` out of
# its import graph deliberately): brain_api already imports the SDK eagerly elsewhere, so
# there is no laziness to preserve here — and a stable module-level name is what tests
# patch to intercept client construction.
from openai import OpenAI

# Sent as the bearer token to a self-hosted server that authenticates nothing. The value
# is arbitrary; the SDK only requires it to be non-empty.
_PLACEHOLDER_KEY = "not-needed"


def base_url(settings: Any) -> str | None:
    """The configured OpenAI-compatible endpoint, or ``None`` for hosted OpenAI.

    Only a real ``str`` counts. Settings are frequently ``MagicMock``\\ ed in tests, and a
    mock attribute is truthy — without the type check it would sail through as a URL and
    fail deep inside httpx instead of here.
    """
    configured = getattr(settings, "openai_base_url", "")
    if not isinstance(configured, str):
        return None
    return configured.strip() or None


def api_key_required(settings: Any) -> bool:
    """Whether a caller must hold an API key before it can reach the model."""
    return base_url(settings) is None


def make_openai(settings: Any, key: str | None, **extra: Any) -> OpenAI:
    """A synchronous ``OpenAI`` bound to the configured endpoint.

    ``extra`` passes through to the SDK constructor (e.g. ``timeout=30.0``).
    """
    url = base_url(settings)
    kwargs: dict[str, Any] = {"api_key": key or (_PLACEHOLDER_KEY if url else "")}
    if url:
        kwargs["base_url"] = url
    kwargs.update(extra)
    return OpenAI(**kwargs)
