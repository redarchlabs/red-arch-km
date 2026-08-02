"""Per-request chat model override + model routing in brain-api.

The API service threads an org's pinned model (orgs.default_llm_model) into the
chat request; these tests prove the override reaches the completion call and
that a routed model (OPENAI_MODEL_ROUTES) is served by a client bound to its
own endpoint rather than the constructor-built default.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from brain_api.openai_client import base_url, model_routes
from brain_api.services.search_service import SearchService
from brain_sdk.vector_store.protocol import SearchResult


@pytest.fixture
def fake_settings() -> MagicMock:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-5-mini"
    settings.openai_base_url = ""
    settings.openai_model_routes = "qwen3-30b=http://127.0.0.1:8099/v1"
    settings.rerank_candidates = 30
    settings.chat_chunk_limit = 10
    return settings


@pytest.fixture
def mock_stores() -> MagicMock:
    stores = MagicMock()
    stores.reranker = None
    stores.embedder.embed.return_value = [0.1, 0.2, 0.3]
    stores.vector.search.return_value = [
        SearchResult(
            id="chunk-1",
            score=0.95,
            payload={
                "text": "Hello world.",
                "document_id": "doc-1",
                "document_key": "key-1",
                "document_title": "Greeting",
                "chunk_order": 0,
            },
        ),
    ]
    stores.graph.fuzzy_relationship_search.return_value = []
    return stores


class TestModelRoutes:
    def test_parses_pairs_and_routed_model_wins(self) -> None:
        settings = MagicMock()
        settings.openai_base_url = "http://global:8000/v1"
        settings.openai_model_routes = "qwen3-30b=http://127.0.0.1:8099/v1, gpt-4.1-mini=https://api.openai.com/v1"
        assert model_routes(settings) == {
            "qwen3-30b": "http://127.0.0.1:8099/v1",
            "gpt-4.1-mini": "https://api.openai.com/v1",
        }
        assert base_url(settings, "qwen3-30b") == "http://127.0.0.1:8099/v1"
        assert base_url(settings, "unrouted-model") == "http://global:8000/v1"

    def test_mock_settings_mean_no_routes(self) -> None:
        # MagicMock attributes are truthy non-strings; both parsers must treat
        # them as unset so the rest of the test suite keeps its old behaviour.
        settings = MagicMock()
        assert model_routes(settings) == {}
        assert base_url(settings) is None

    def test_original_casing_is_preserved_with_case_insensitive_lookup(self) -> None:
        # The catalog serves these keys to admins, who store + send them verbatim
        # as the literal model id — a case-sensitive server must see the exact
        # configured spelling, while lookups still match any casing.
        settings = MagicMock()
        settings.openai_base_url = ""
        settings.openai_model_routes = "Qwen3-30B-Local=http://127.0.0.1:8099/v1"
        assert list(model_routes(settings)) == ["Qwen3-30B-Local"]
        assert base_url(settings, "qwen3-30b-local") == "http://127.0.0.1:8099/v1"


class TestAgenticModelOverride:
    """The fact-engine agent honours the per-org pin too (routed openai client)."""

    def _settings(self) -> MagicMock:
        settings = MagicMock()
        settings.resolved_agent_model = "gpt-5-mini"
        settings.openai_api_key = "sk-test"
        settings.openai_base_url = ""
        settings.openai_model_routes = "qwen3-30b=http://127.0.0.1:8099/v1"
        return settings

    def test_override_builds_a_routed_openai_client_and_caches_it(self) -> None:
        from brain_api.stores import Stores

        stores = Stores(self._settings())
        with patch("brain_api.stores.make_llm_client") as fake:
            first = stores.llm_for_model("qwen3-30b")
            second = stores.llm_for_model("qwen3-30b")
        fake.assert_called_once_with(
            provider="openai",
            model="qwen3-30b",
            api_key="sk-test",
            base_url="http://127.0.0.1:8099/v1",
        )
        assert first is second

    def test_no_override_or_default_model_uses_the_shared_llm(self) -> None:
        from brain_api.stores import Stores

        stores = Stores(self._settings())
        with patch("brain_api.stores.make_llm_client") as fake:
            default = stores.llm
            assert stores.llm_for_model(None) is default
            assert stores.llm_for_model("gpt-5-mini") is default
        fake.assert_called_once()  # only the shared default client was built


class TestVectorChatModelOverride:
    def test_override_reaches_the_completion_call(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.vector_chat(tenant_id="t1", query="hello", model="gpt-4.1-mini", use_knowledge_graph=False)
        call = fake_openai.return_value.chat.completions.create.call_args
        assert call.kwargs["model"] == "gpt-4.1-mini"

    def test_no_override_keeps_the_configured_default(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.vector_chat(tenant_id="t1", query="hello", use_knowledge_graph=False)
        call = fake_openai.return_value.chat.completions.create.call_args
        assert call.kwargs["model"] == "gpt-5-mini"

    def test_routed_model_gets_a_client_bound_to_its_endpoint(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.vector_chat(tenant_id="t1", query="hello", model="qwen3-30b", use_knowledge_graph=False)
        # First construction is the default client (no route → hosted), the second
        # is the routed one pointed at the model's own endpoint.
        urls = [c.kwargs.get("base_url") for c in fake_openai.call_args_list]
        assert urls[-1] == "http://127.0.0.1:8099/v1"

    def test_default_model_coinciding_with_a_route_is_not_rerouted(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        # The configured default name may also appear in OPENAI_MODEL_ROUTES
        # (llama.cpp serves any name; ops route that id to hosted for pinned
        # orgs). Without an explicit pin the unpinned path must stay on the
        # constructor-built default client — not silently move to the route.
        fake_settings.openai_model_routes = "gpt-5-mini=https://api.openai.com/v1"
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.vector_chat(tenant_id="t1", query="hello", use_knowledge_graph=False)
        assert fake_openai.call_count == 1  # default client only
        assert fake_openai.call_args_list[0].kwargs.get("base_url") is None

    def test_unrouted_model_reuses_the_default_client(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.vector_chat(tenant_id="t1", query="hello", model="gpt-4.1-mini", use_knowledge_graph=False)
        # Same endpoint → no second client construction.
        assert fake_openai.call_count == 1

    def test_stream_honours_the_override(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            fake_openai.return_value.chat.completions.create.return_value = iter([])
            service = SearchService(mock_stores, fake_settings)
            list(
                service.vector_chat_stream(
                    tenant_id="t1", query="hello", model="qwen3-30b", use_knowledge_graph=False
                )
            )
        call = fake_openai.return_value.chat.completions.create.call_args
        assert call.kwargs["model"] == "qwen3-30b"
        assert call.kwargs["stream"] is True
