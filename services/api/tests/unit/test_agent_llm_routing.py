"""An agent's model decides which server answers it.

Before this, the agent path built its provider with no ``api_base`` and let
LiteLLM fall back to ``OPENAI_BASE_URL``. On a host pointed at a local llama.cpp
server that sent *every* OpenAI-shaped model to the local box whatever it was
called — so pinning an agent to ``gpt-4.1-mini`` quietly asked a Qwen server for
a model it does not have.

``OPENAI_MODEL_ROUTES`` already decides this for workflow nodes and brain-api.
These pin the agent path to the same answer, so an agent and a workflow naming the
same model reach the same server.
"""

from __future__ import annotations

import pytest
from api.services.agents.llm.routing import bare_model, provider_for

pytestmark = pytest.mark.unit


class _Settings:
    def __init__(self, routes: str = "", base: str = "") -> None:
        self.openai_model_routes = routes
        self.openai_base_url = base


ROUTES = "qwen3-30b=http://127.0.0.1:8099/v1, gpt-4.1-mini=https://api.openai.com/v1"


class TestModelRouting:
    def test_a_hosted_model_goes_to_openai_even_on_a_local_box(self) -> None:
        # The failure this exists to stop: the global endpoint is the local
        # server, so without a route the request would have gone there.
        settings = _Settings(routes=ROUTES, base="http://127.0.0.1:8099/v1")

        provider = provider_for(settings, "openai/gpt-4.1-mini", "sk-test")

        assert provider.default_params["api_base"] == "https://api.openai.com/v1"

    def test_a_local_model_still_goes_local(self) -> None:
        settings = _Settings(routes=ROUTES, base="https://api.openai.com/v1")

        provider = provider_for(settings, "openai/qwen3-30b", None)

        assert provider.default_params["api_base"] == "http://127.0.0.1:8099/v1"

    def test_an_unrouted_model_falls_back_to_the_global_endpoint(self) -> None:
        settings = _Settings(routes=ROUTES, base="http://127.0.0.1:8099/v1")

        provider = provider_for(settings, "openai/some-other-model", None)

        assert provider.default_params["api_base"] == "http://127.0.0.1:8099/v1"

    def test_no_endpoint_configured_means_hosted_openai(self) -> None:
        # An empty api_base must not be sent: LiteLLM's own default is correct.
        provider = provider_for(_Settings(), "openai/gpt-4.1-mini", "sk-test")

        assert provider.default_params == {}

    def test_anthropic_is_left_alone(self) -> None:
        """Passing an OpenAI-shaped api_base to Claude would point it at the wrong
        server entirely."""
        settings = _Settings(routes=ROUTES, base="http://127.0.0.1:8099/v1")

        provider = provider_for(settings, "anthropic/claude-sonnet-5", "sk-ant")

        assert provider.default_params == {}
        assert provider.api_key == "sk-ant"


class TestBareModel:
    def test_strips_the_litellm_prefix_the_routes_table_does_not_use(self) -> None:
        # Agents carry `openai/gpt-4.1-mini`; the routes table is keyed the way a
        # workflow node names the model.
        assert bare_model("openai/gpt-4.1-mini") == "gpt-4.1-mini"
        assert bare_model("gpt-4.1-mini") == "gpt-4.1-mini"
