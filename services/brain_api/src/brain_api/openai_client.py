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


def model_routes(settings: Any) -> dict[str, str]:
    """Parse ``OPENAI_MODEL_ROUTES`` into ``{model id: base URL}``.

    Mirrors the API service's parser: ``model=url`` pairs separated by commas or
    whitespace. One self-hosted server serves ONE loaded model, so "pick a model"
    only means something if different ids can reach different endpoints — this is
    what lets a per-org model pin route to local llama.cpp or hosted OpenAI.
    Malformed entries are skipped; unset means every model goes to
    ``OPENAI_BASE_URL`` as before.

    Keys keep the operator's ORIGINAL casing — the caller sends the model string
    verbatim to the serving endpoint, and a case-sensitive server must see the
    exact configured spelling. Lookups are case-insensitive in :func:`base_url`.
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
    """Whether a caller must hold an API key before it can reach the model.

    A routed model is judged by ITS endpoint, so a hybrid setup (hosted OpenAI
    plus a local model) still refuses to run keyless against OpenAI while the
    local route needs nothing.
    """
    return base_url(settings, model) is None


def make_openai(settings: Any, key: str | None, *, model: str | None = None, **extra: Any) -> OpenAI:
    """A synchronous ``OpenAI`` bound to the endpoint serving ``model``.

    Pass ``model`` whenever the caller has already resolved which model it will
    ask for, so a routed model reaches its own server. ``extra`` passes through
    to the SDK constructor (e.g. ``timeout=30.0``).
    """
    url = base_url(settings, model)
    kwargs: dict[str, Any] = {"api_key": key or (_PLACEHOLDER_KEY if url else "")}
    if url:
        kwargs["base_url"] = url
    kwargs.update(extra)
    return OpenAI(**kwargs)
