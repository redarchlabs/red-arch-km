"""Embeddings must go where they are configured to go — and nowhere else.

These tests exist because of a real incident. ``OPENAI_BASE_URL`` was set to redirect
*chat* at a local llama.cpp server; the embedding provider constructed its client as
``OpenAI(api_key=...)`` with no ``base_url``, the SDK fell back to that same environment
variable, and every embedding request went to a chat-only server that answers
``501 This server does not support embeddings``. Document ingest broke, and the failure
looked like a connectivity problem rather than a routing one.

The invariant being protected: base_url is always passed explicitly, so a process-wide
environment variable can never silently reroute a call.
"""

from __future__ import annotations

import pytest
from brain_sdk.embedding.openai_provider import OPENAI_API_BASE, OpenAIEmbeddingProvider


class TestExplicitBaseUrl:
    def test_defaults_to_openai_even_when_env_var_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The regression itself: a chat-server env var must not capture embeddings."""
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8099/v1")
        provider = OpenAIEmbeddingProvider(api_key="sk-test")
        assert str(provider._client.base_url).rstrip("/") == OPENAI_API_BASE

    def test_explicit_base_url_is_used(self) -> None:
        provider = OpenAIEmbeddingProvider(
            api_key="", model="nomic-embed-text-v1.5", base_url="http://127.0.0.1:8098/v1", dimension=768
        )
        assert str(provider._client.base_url).rstrip("/") == "http://127.0.0.1:8098/v1"
        assert provider.base_url == "http://127.0.0.1:8098/v1"

    def test_blank_base_url_is_treated_as_unset(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test", base_url="   ")
        assert str(provider._client.base_url).rstrip("/") == OPENAI_API_BASE

    def test_self_hosted_gets_placeholder_key(self) -> None:
        """Local servers authenticate nothing, but the SDK still requires a key."""
        provider = OpenAIEmbeddingProvider(api_key="", base_url="http://127.0.0.1:8098/v1", dimension=768)
        assert provider._client.api_key == "not-needed"


class TestDimension:
    def test_known_openai_models_resolve(self) -> None:
        assert OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-small").dimension == 1536
        assert OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-large").dimension == 3072

    def test_explicit_dimension_wins(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-small", dimension=768)
        assert provider.dimension == 768

    def test_self_hosted_without_dimension_is_rejected(self) -> None:
        """Silently guessing 1536 would build the vector store at the wrong width.

        That corrupts retrieval instead of raising, so refuse at construction instead.
        """
        with pytest.raises(ValueError, match="dimension is required"):
            OpenAIEmbeddingProvider(api_key="", model="nomic-embed-text-v1.5", base_url="http://127.0.0.1:8098/v1")

    def test_zero_dimension_is_treated_as_unset(self) -> None:
        # 0 is the settings default, meaning "not configured" — not a real width.
        with pytest.raises(ValueError, match="dimension is required"):
            OpenAIEmbeddingProvider(api_key="", model="custom", base_url="http://localhost:1/v1", dimension=0)
