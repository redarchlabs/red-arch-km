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

from ipaddress import ip_address
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:  # pragma: no cover - import cycle / heavy dep kept out of runtime
    from openai import AsyncOpenAI, OpenAI

# Sent as the bearer token when talking to a self-hosted server that does not authenticate.
# The value is arbitrary; the SDK only requires that it be non-empty.
_PLACEHOLDER_KEY = "not-needed"


def model_routes(settings: Any) -> dict[str, str]:
    """Parse ``OPENAI_MODEL_ROUTES`` into ``{model id: base URL}``.

    One self-hosted server serves ONE loaded model, so "pick a model" only means
    something if different model ids can reach different endpoints. A fast small
    model for condensing retrieved passages and a large one for reasoning are
    different processes on different ports; this maps the id a caller asks for
    onto the server that actually has it.

    Format is ``model=url`` pairs separated by commas or whitespace::

        OPENAI_MODEL_ROUTES=qwen3-4b=http://127.0.0.1:8097/v1, qwen3-30b=http://127.0.0.1:8099/v1

    Unset (the default) means every model goes to ``OPENAI_BASE_URL`` as before.
    Malformed entries are skipped rather than raising: a typo in one route must not
    take the whole service down, and the fallback is the plain global endpoint.

    Keys keep the operator's ORIGINAL casing: they leave this module (the org
    model-pin catalog serves them to admins, who store and send them verbatim as
    the literal ``model`` field), and a case-sensitive server (vLLM's
    ``--served-model-name``, hosted APIs) must see the exact configured spelling.
    Lookups are case-insensitive in :func:`base_url`.
    """
    configured = getattr(settings, "openai_model_routes", "")
    if not isinstance(configured, str) or not configured.strip():
        return {}
    routes: dict[str, str] = {}
    for entry in configured.replace(",", " ").split():
        model, _, url = entry.partition("=")
        model, url = model.strip(), url.strip()
        if model and url:
            routes[model] = url
    return routes


def base_url(settings: Any, model: str | None = None) -> str | None:
    """The OpenAI-compatible endpoint for ``model``, or ``None`` for hosted OpenAI.

    Accepts any settings object exposing ``openai_base_url`` so the API service and the
    worker/brain services can share the convention without sharing a Settings class.
    A ``model`` with an entry in :func:`model_routes` wins over the global endpoint.

    Only a real ``str`` counts. Settings are frequently ``MagicMock``\\ ed in tests, and a
    mock attribute is truthy — without the type check it would sail through as a URL and
    fail deep inside httpx instead of here.
    """
    if model:
        wanted = model.strip().lower()
        # Case-insensitive match against original-cased keys (see model_routes).
        routed = next((url for key, url in model_routes(settings).items() if key.lower() == wanted), None)
        if routed:
            return routed
    configured = getattr(settings, "openai_base_url", "")
    if not isinstance(configured, str):
        return None
    return configured.strip() or None


def api_key_required(settings: Any, model: str | None = None) -> bool:
    """Resolve authentication with the endpoint, shared by workflows and agents.

    Hosted OpenAI always requires a key. Private/loopback endpoints default to
    keyless for existing local deployments; public proxies require credentials.
    OPENAI_MODEL_AUTH overrides custom endpoints with model=required|none pairs
    (or *=required|none as a deployment default).
    """
    url = base_url(settings, model)
    if not url:
        return True
    host = (urlparse(url).hostname or "").lower()
    if not host or host == "api.openai.com" or host.endswith(".openai.com"):
        return True
    configured = getattr(settings, "openai_model_auth", "")
    if isinstance(configured, str):
        policies = dict(entry.split("=", 1) for entry in configured.replace(",", " ").split() if "=" in entry)
        policy = next((v for k, v in policies.items() if model and k.lower() == model.strip().lower()), None)
        policy = policy or policies.get("*")
        if policy in {"required", "none"}:
            return policy == "required"
    if host == "localhost" or host.endswith((".localhost", ".internal")):
        return False
    try:
        address = ip_address(host)
    except ValueError:
        # Single-label names address Docker services on a private network.
        return "." in host
    return not (address.is_private or address.is_loopback)


def _kwargs(settings: Any, key: str | None, model: str | None, extra: dict[str, Any]) -> dict[str, Any]:
    url = base_url(settings, model)
    # The SDK rejects an empty api_key, so fall back to a placeholder the local server drops.
    kwargs: dict[str, Any] = {"api_key": key or (_PLACEHOLDER_KEY if not api_key_required(settings, model) else "")}
    if url:
        kwargs["base_url"] = url
    kwargs.update(extra)
    return kwargs


def make_async_openai(settings: Any, key: str | None, *, model: str | None = None, **extra: Any) -> AsyncOpenAI:
    """An ``AsyncOpenAI`` bound to the endpoint serving ``model``.

    Pass ``model`` whenever the caller has already resolved which model it will ask for,
    so a routed model reaches its own server. ``extra`` passes through to the SDK
    constructor (e.g. ``timeout=30.0``).
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(**_kwargs(settings, key, model, extra))


def make_openai(settings: Any, key: str | None, *, model: str | None = None, **extra: Any) -> OpenAI:
    """A synchronous ``OpenAI`` bound to the endpoint serving ``model``."""
    from openai import OpenAI

    return OpenAI(**_kwargs(settings, key, model, extra))
