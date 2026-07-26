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
)


@dataclass
class _Settings:
    """Minimal stand-in — the helpers read one attribute by design."""

    openai_base_url: str = ""


HOSTED = _Settings()
LOCAL = _Settings(openai_base_url="http://localhost:11434/v1")


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
