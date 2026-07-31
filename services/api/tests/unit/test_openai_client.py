"""OPENAI_BASE_URL wiring: hosted OpenAI stays byte-identical, local endpoints work keyless.

The regression these guard against is subtle: every LLM call site refuses to run without an
API key, but a self-hosted server (Ollama/vLLM/llama.cpp) authenticates nothing — so a fully
local deployment would fail asking for a credential it will never use.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from api.services.openai_client import (
    api_key_required,
    base_url,
    make_async_openai,
    make_openai,
    model_routes,
)


@dataclass
class _Settings:
    """Minimal stand-in — the helpers read one attribute by design."""

    openai_base_url: str = ""
    openai_model_routes: str = ""


HOSTED = _Settings()
LOCAL = _Settings(openai_base_url="http://localhost:11434/v1")
# A small model on its own port beside the big one: what makes the answer-model
# dropdown mean something when each server serves exactly one loaded model.
ROUTED = _Settings(
    openai_base_url="http://127.0.0.1:8099/v1",
    openai_model_routes="qwen3-4b-fast=http://127.0.0.1:8097/v1",
)


class TestBaseUrl:
    def test_unset_means_hosted_openai(self) -> None:
        assert base_url(HOSTED) is None

    def test_configured_url_is_returned(self) -> None:
        assert base_url(LOCAL) == "http://localhost:11434/v1"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_whitespace_only_is_treated_as_unset(self, blank: str) -> None:
        # A stray space in an .env file must not silently redirect every call.
        assert base_url(_Settings(openai_base_url=blank)) is None

    def test_surrounding_whitespace_is_stripped(self) -> None:
        assert base_url(_Settings(openai_base_url="  http://host/v1  ")) == "http://host/v1"

    def test_missing_attribute_is_tolerated(self) -> None:
        # Settings objects that predate the field (or other services') must not explode.
        assert base_url(object()) is None

    def test_non_string_is_ignored(self) -> None:
        # Settings are routinely MagicMock'd in tests and a mock attribute is truthy.
        # Without this guard the mock sails through as a URL and blows up inside httpx.
        from unittest.mock import MagicMock

        assert base_url(MagicMock()) is None
        assert api_key_required(MagicMock()) is True


class TestApiKeyRequired:
    def test_required_for_hosted_openai(self) -> None:
        assert api_key_required(HOSTED) is True

    def test_not_required_for_local_endpoint(self) -> None:
        assert api_key_required(LOCAL) is False


class TestClientConstruction:
    def test_hosted_client_gets_no_base_url_override(self) -> None:
        client = make_async_openai(HOSTED, "sk-real-key")
        assert client.api_key == "sk-real-key"
        assert "openai.com" in str(client.base_url)

    def test_local_client_targets_the_configured_endpoint(self) -> None:
        client = make_async_openai(LOCAL, "sk-real-key")
        assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"

    def test_local_client_builds_without_a_key(self) -> None:
        # The whole point: no OpenAI credential, still a usable client.
        client = make_async_openai(LOCAL, "")
        assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"
        assert client.api_key  # non-empty placeholder; the SDK rejects a blank key

    def test_extra_kwargs_pass_through(self) -> None:
        # dispatcher.py pins timeout=30.0 to avoid holding a pooled DB connection.
        client = make_async_openai(HOSTED, "sk-real-key", timeout=30.0)
        assert client.timeout == 30.0

    def test_sync_client_follows_the_same_rules(self) -> None:
        client = make_openai(LOCAL, "")
        assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"
        assert client.api_key


class TestSettingsField:
    def test_defaults_to_empty_so_production_is_unchanged(self) -> None:
        from api.config import Settings

        assert Settings(openai_base_url="").openai_base_url == ""
        assert api_key_required(Settings(openai_base_url="")) is True


class TestModelRoutes:
    """Per-model endpoints. One llama.cpp process serves ONE loaded model, so asking for
    a different model id only means something if it can reach a different server."""

    def test_unset_is_no_routes(self) -> None:
        assert model_routes(HOSTED) == {}

    def test_parses_comma_and_space_separated_pairs(self) -> None:
        settings = _Settings(openai_model_routes="a=http://x/v1, b=http://y/v1  c=http://z/v1")
        assert model_routes(settings) == {"a": "http://x/v1", "b": "http://y/v1", "c": "http://z/v1"}

    def test_model_ids_match_case_insensitively(self) -> None:
        settings = _Settings(openai_model_routes="Qwen3-4B-Fast=http://x/v1")
        assert base_url(settings, "qwen3-4b-FAST") == "http://x/v1"

    def test_malformed_entries_are_skipped_not_fatal(self) -> None:
        """A typo in one route must not take every LLM call down with it — the caller
        falls back to the global endpoint instead."""
        settings = _Settings(
            openai_base_url="http://global/v1",
            openai_model_routes="broken, =http://nomodel/v1, ok=http://x/v1, alsobroken=",
        )
        assert model_routes(settings) == {"ok": "http://x/v1"}
        assert base_url(settings, "broken") == "http://global/v1"

    def test_non_string_setting_is_ignored(self) -> None:
        # Settings are frequently MagicMocked; a mock attribute is truthy.
        assert model_routes(_Settings(openai_model_routes=object())) == {}  # type: ignore[arg-type]


class TestRoutedBaseUrl:
    def test_routed_model_wins_over_the_global_endpoint(self) -> None:
        assert base_url(ROUTED, "qwen3-4b-fast") == "http://127.0.0.1:8097/v1"

    def test_unrouted_model_falls_back_to_the_global_endpoint(self) -> None:
        assert base_url(ROUTED, "qwen3-30b") == "http://127.0.0.1:8099/v1"

    def test_no_model_argument_is_the_old_behaviour(self) -> None:
        assert base_url(ROUTED) == "http://127.0.0.1:8099/v1"

    def test_client_is_bound_to_the_routed_endpoint(self) -> None:
        client = make_async_openai(ROUTED, None, model="qwen3-4b-fast")
        assert str(client.base_url).rstrip("/") == "http://127.0.0.1:8097/v1"
        sync = make_openai(ROUTED, None, model="qwen3-4b-fast")
        assert str(sync.base_url).rstrip("/") == "http://127.0.0.1:8097/v1"

    def test_routed_local_model_needs_no_key_while_hosted_still_does(self) -> None:
        """A hybrid deployment: hosted OpenAI for the big model, a local small one for
        spoken answers. The key requirement follows the endpoint the model resolves to."""
        hybrid = _Settings(openai_model_routes="local-fast=http://127.0.0.1:8097/v1")
        assert api_key_required(hybrid, "gpt-5-mini") is True
        assert api_key_required(hybrid, "local-fast") is False
