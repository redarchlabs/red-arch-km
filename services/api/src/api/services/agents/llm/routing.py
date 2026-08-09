"""Build an :class:`LLMProvider` that talks to the endpoint the model belongs to.

The agent path used to construct ``LLMProvider(api_key=key)`` with no ``api_base``,
which hands the decision to LiteLLM — and LiteLLM reads ``OPENAI_BASE_URL`` from
the process environment. On a host pointed at a local llama.cpp server that means
*every* OpenAI-shaped model went to the local box, whatever it was called: naming
``gpt-4.1-mini`` on an agent sent "gpt-4.1-mini" to a server that serves Qwen.

Routing is not new — ``OPENAI_MODEL_ROUTES`` already decides this for workflow LLM
nodes and for brain-api. This reuses that resolver so an agent and a workflow that
name the same model reach the same server, rather than the agent path having its
own accidental answer.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.llm.catalog import bare_model
from api.services.agents.llm.provider import LLMProvider
from api.services.openai_client import base_url

# LiteLLM needs the provider prefix on the agent path (``openai/gpt-4.1-mini``);
# the routes table is keyed by the bare model id, as workflow nodes name it, so
# `bare_model` (from the catalog) is what bridges the two.
__all__ = ["bare_model", "provider_for"]


def provider_for(settings: Any, model: str, api_key: str | None) -> LLMProvider:
    """An ``LLMProvider`` pinned to the endpoint this model is routed to.

    A routed model wins over the global endpoint, so one deployment serves local
    and hosted models side by side. Anthropic and Gemini are left alone — their
    endpoints are not ``OPENAI_BASE_URL``'s business, and passing an ``api_base``
    to them would point a Claude call at an OpenAI-shaped server.
    """
    if not model.startswith("openai/") and "/" in model:
        return LLMProvider(api_key=api_key)
    endpoint = base_url(settings, bare_model(model))
    return LLMProvider(api_key=api_key, default_params={"api_base": endpoint} if endpoint else {})
