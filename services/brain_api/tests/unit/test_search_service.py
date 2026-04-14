"""Tests for SearchService with mocked dependencies."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from brain_api.services.search_service import SearchService
from brain_sdk.vector_store.protocol import SearchResult


@pytest.fixture
def fake_settings() -> MagicMock:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-4.1-mini"
    return settings


@pytest.fixture
def mock_stores() -> MagicMock:
    stores = MagicMock()
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
    stores.graph.fuzzy_relationship_search.return_value = [
        {"subj": "Alice", "pred": "knows", "obj": "Bob"},
    ]
    return stores


class TestVectorSearch:
    def test_returns_hits(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.services.search_service.OpenAI"):
            service = SearchService(mock_stores, fake_settings)

        result = service.vector_search(
            tenant_id="t1", query="hello", limit=5, access_keys=[1, 2], tags=["tag"]
        )
        assert result["total"] == 1
        assert result["hits"][0]["id"] == "chunk-1"

        mock_stores.embedder.embed.assert_called_once_with("hello")
        mock_stores.vector.search.assert_called_once()
        call = mock_stores.vector.search.call_args
        assert call.kwargs["tenant_id"] == "t1"
        assert call.kwargs["access_keys"] == [1, 2]
        assert call.kwargs["required_tags"] == ["tag"]


class TestVectorChatStream:
    def test_emits_sources_then_graph_then_done(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        with patch("brain_api.services.search_service.OpenAI") as mock_openai:
            fake_client = MagicMock()
            mock_openai.return_value = fake_client

            # Mock streaming response: one delta chunk, then stop
            delta = MagicMock()
            delta.choices = [MagicMock(delta=MagicMock(content="Hi!"))]
            stop = MagicMock()
            stop.choices = [MagicMock(delta=MagicMock(content=None))]
            fake_client.chat.completions.create.return_value = iter([delta, stop])

            service = SearchService(mock_stores, fake_settings)
            events = list(
                service.vector_chat_stream(
                    tenant_id="t1", query="hello", use_knowledge_graph=True
                )
            )

        event_types = [e["type"] for e in events]
        assert event_types[0] == "sources"
        assert event_types[1] == "graph"
        assert "delta" in event_types
        assert event_types[-1] == "done"

    def test_graph_disabled(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.services.search_service.OpenAI") as mock_openai:
            fake_client = MagicMock()
            mock_openai.return_value = fake_client
            fake_client.chat.completions.create.return_value = iter([])

            service = SearchService(mock_stores, fake_settings)
            events = list(
                service.vector_chat_stream(
                    tenant_id="t1", query="hello", use_knowledge_graph=False
                )
            )

        # Graph event still emitted but with empty list; graph store not queried
        graph_events = [e for e in events if e["type"] == "graph"]
        assert len(graph_events) == 1
        assert graph_events[0]["triplets"] == []
        mock_stores.graph.fuzzy_relationship_search.assert_not_called()

    def test_retrieval_error_emits_error_event(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        mock_stores.embedder.embed.side_effect = RuntimeError("embedding failed")
        with patch("brain_api.services.search_service.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
            events = list(service.vector_chat_stream(tenant_id="t1", query="hi"))

        assert events == [{"type": "error", "message": "Retrieval failed"}]
