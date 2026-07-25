"""Build the OpenAI SDK client, optionally pointed at a self-hosted endpoint.

KM2 reaches OpenAI through the official SDK, and that SDK speaks plain HTTP to whatever
``base_url`` it is handed — so an OpenAI-compatible server (Ollama, vLLM, llama.cpp,
LM Studio) is a *configuration* change rather than a code change. Setting
``OPENAI_BASE_URL`` redirects every call in this service at that server; leaving it unset
keeps the hosted-OpenAI behaviour byte for byte, so existing deployments are untouched.

This module exists to hide one wrinkle that would otherwise be copy-pasted across every
call site. A self-hosted endpoint needs **no credential**, but:

* the SDK still requires a non-empty ``api_key`` (it sends it as a bearer token the local
  server ignores), and
* every call site currently *refuses to run* without a key — "requires an OpenAI API key"

…so a fully local deployment would fail asking for a key it will never use.
:func:`api_key_required` lets callers ask whether a key is genuinely mandatory, and
:func:`make_async_openai` supplies a harmless placeholder when it is not.

Usage mirrors the existing key-resolution convention (org key, then central key)::

    key = await self._org_openai_key(org_id) or settings.openai_api_key.get_secret_value()
    if not key and api_key_required(settings):
        raise ActionError("… requires an OpenAI API key (org or central)")
    client = make_async_openai(settings, key)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle / heavy dep kept out of runtime
    from openai import AsyncOpenAI, OpenAI

# Sent as the bearer token when talking to a self-hosted server that does not authenticate.
# The value is arbitrary; the SDK only requires that it be non-empty.
_PLACEHOLDER_KEY = "not-needed"


def base_url(settings: Any) -> str | None:
    """The configured OpenAI-compatible endpoint, or ``None`` for hosted OpenAI.

    Accepts any settings object exposing ``openai_base_url`` so the API service and the
    worker/brain services can share the convention without sharing a Settings class.

    Only a real ``str`` counts. Settings are frequently ``MagicMock``\\ ed in tests, and a
    mock attribute is truthy — without the type check it would sail through as a URL and
    fail deep inside httpx instead of here.
    """
    configured = getattr(settings, "openai_base_url", "")
    if not isinstance(configured, str):
        return None
    return configured.strip() or None


def api_key_required(settings: Any) -> bool:
    """Whether a caller must have an API key before it can talk to the model.

    False when pointed at a self-hosted endpoint — local servers authenticate nothing, and
    demanding a key there is how "run everything locally" fails on its first request.
    """
    return base_url(settings) is None


def _kwargs(settings: Any, key: str | None, extra: dict[str, Any]) -> dict[str, Any]:
    url = base_url(settings)
    # The SDK rejects an empty api_key, so fall back to a placeholder the local server drops.
    kwargs: dict[str, Any] = {"api_key": key or (_PLACEHOLDER_KEY if url else "")}
    if url:
        kwargs["base_url"] = url
    kwargs.update(extra)
    return kwargs


def make_async_openai(settings: Any, key: str | None, **extra: Any) -> AsyncOpenAI:
    """An ``AsyncOpenAI`` bound to the configured endpoint.

    ``extra`` passes through to the SDK constructor (e.g. ``timeout=30.0``).
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(**_kwargs(settings, key, extra))


def make_openai(settings: Any, key: str | None, **extra: Any) -> OpenAI:
    """A synchronous ``OpenAI`` bound to the configured endpoint."""
    from openai import OpenAI

    return OpenAI(**_kwargs(settings, key, extra))
