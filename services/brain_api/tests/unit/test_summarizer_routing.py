"""Summariser endpoint resolution and the whole-corpus-egress guard.

Summarising is one LLM call per chunk over EVERY ingested document, so where it
points is a cost/egress decision of a different order than chat's one call per
question. These tests pin two things: the summariser resolves through the shared
model-routing table (so it can be given its own model), and it cannot silently
end up on a metered endpoint in a deployment that declared itself local.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from brain_api.openai_client import is_local_endpoint
from brain_api.stores import Stores

LOCAL_CHAT = "http://172.22.0.1:8099/v1"
LOCAL_FAST = "http://172.22.0.1:8097/v1"
HOSTED = "https://api.openai.com/v1"
ROUTES = f"qwen3-30b={LOCAL_CHAT} qwen3-4b-fast={LOCAL_FAST} gpt-4.1-mini={HOSTED}"


def _settings(**overrides):  # type: ignore[no-untyped-def]
    """A settings double with the fields the summariser property reads."""
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-4.1-mini"
    settings.openai_base_url = LOCAL_CHAT
    settings.openai_model_routes = ROUTES
    settings.summarizer_model = ""
    settings.summarizer_require_local = False
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _build(settings):  # type: ignore[no-untyped-def]
    """Build the summariser, capturing the ChunkSummarizer kwargs."""
    with patch("brain_api.stores.ChunkSummarizer") as summarizer_cls:
        _ = Stores(settings).summarizer
    return summarizer_cls.call_args.kwargs


class TestIsLocalEndpoint:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8099/v1",
            "http://localhost:8099/v1",
            LOCAL_CHAT,  # docker gateway, RFC1918
            "http://192.168.1.50:8099/v1",
            "http://llama:8099/v1",  # compose service name
        ],
    )
    def test_local_forms(self, url: str) -> None:
        assert is_local_endpoint(url) is True

    @pytest.mark.parametrize("url", [HOSTED, "https://api.anthropic.com/v1", "http://8.8.8.8/v1"])
    def test_hosted_forms(self, url: str) -> None:
        assert is_local_endpoint(url) is False

    def test_none_is_not_local(self) -> None:
        # None = no base_url = the SDK's hosted default. The whole point of the
        # guard is that this case must not read as "local".
        assert is_local_endpoint(None) is False


class TestSummarizerRouting:
    def test_unset_model_preserves_previous_behaviour(self) -> None:
        # Historical behaviour: chat model, at the deployment's base URL. It must
        # NOT start resolving 'gpt-4.1-mini' through the routes (which would flip
        # every existing local deployment's ingest over to hosted OpenAI).
        kwargs = _build(_settings())
        assert kwargs["base_url"] == LOCAL_CHAT
        assert kwargs["model"] == "gpt-4.1-mini"

    def test_pinned_model_routes_to_its_own_server(self) -> None:
        kwargs = _build(_settings(summarizer_model="qwen3-4b-fast"))
        assert kwargs["base_url"] == LOCAL_FAST
        assert kwargs["model"] == "qwen3-4b-fast"

    def test_placeholder_key_when_local_and_keyless(self) -> None:
        kwargs = _build(_settings(openai_api_key=""))
        assert kwargs["api_key"] == "not-needed"


class TestEgressGuard:
    def test_require_local_rejects_hosted_pin(self) -> None:
        settings = _settings(summarizer_model="gpt-4.1-mini", summarizer_require_local=True)
        with pytest.raises(ValueError, match="SUMMARIZER_REQUIRE_LOCAL"):
            _build(settings)

    def test_require_local_rejects_missing_base_url(self) -> None:
        # The original failure mode: OPENAI_BASE_URL absent, so every chunk
        # summary silently bills to hosted OpenAI.
        settings = _settings(openai_base_url="", openai_model_routes="", summarizer_require_local=True)
        with pytest.raises(ValueError, match="whole-corpus egress"):
            _build(settings)

    def test_require_local_allows_local_pin(self) -> None:
        settings = _settings(summarizer_model="qwen3-4b-fast", summarizer_require_local=True)
        assert _build(settings)["base_url"] == LOCAL_FAST

    def test_hosted_deployment_unaffected(self) -> None:
        # No self-hosted endpoint declared and no assertion made: a normal hosted
        # deployment must keep working, with no error.
        settings = _settings(openai_base_url="", openai_model_routes="")
        assert _build(settings)["base_url"] is None

    def test_mismatch_without_flag_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = _settings(summarizer_model="gpt-4.1-mini")
        with caplog.at_level("WARNING"):
            assert _build(settings)["base_url"] == HOSTED
        assert "bill to that API" in caplog.text
