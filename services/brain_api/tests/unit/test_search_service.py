"""Tests for SearchService with mocked dependencies."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from brain_api.services.search_service import SearchService, _snippet
from brain_sdk.vector_store.protocol import SearchResult


def _hit(number: int, *, doc_key: str, section: str | None, chunk_order: int, text: str = "body") -> dict:
    return {
        "id": f"chunk-{number}",
        "score": 0.9 - number * 0.01,
        "payload": {
            "text": text,
            "document_id": f"id-{doc_key}",
            "document_key": doc_key,
            "document_title": doc_key.title(),
            "section": section,
            "chunk_order": chunk_order,
        },
    }


class TestSnippet:
    def test_short_text_passes_through(self) -> None:
        assert _snippet("Hello world.") == "Hello world."

    def test_collapses_whitespace(self) -> None:
        assert _snippet("Hello   \n  world.") == "Hello world."

    def test_truncates_on_word_boundary_with_ellipsis(self) -> None:
        text = "word " * 100
        out = _snippet(text, max_chars=20)
        assert out.endswith("…")
        assert len(out) <= 21
        assert not out[:-1].endswith(" ")


class TestPassageSources:
    def test_one_source_per_passage_numbered_in_order(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.services.search_service.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = [
            _hit(1, doc_key="nt", section="Matthew 4", chunk_order=7, text="Feeding the five thousand."),
            _hit(2, doc_key="nt", section="Mark 8", chunk_order=20, text="Whom do men say that I am?"),
        ]
        sources = service._passage_sources(hits)
        # Two passages of the SAME document get distinct numbers (the whole point).
        assert [s["number"] for s in sources] == [1, 2]
        assert [s["section"] for s in sources] == ["Matthew 4", "Mark 8"]
        assert [s["chunk_order"] for s in sources] == [7, 20]
        assert all(s["document_key"] == "nt" for s in sources)
        assert sources[0]["snippet"] == "Feeding the five thousand."

    def test_section_absent_is_none(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.services.search_service.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = [{"id": "c", "score": 0.5, "payload": {"text": "t", "document_key": "d"}}]
        sources = service._passage_sources(hits)
        assert sources[0]["section"] is None
        assert sources[0]["chunk_order"] is None


class TestFormatContext:
    def test_numbers_per_passage_with_section_label(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.services.search_service.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = [
            _hit(1, doc_key="nt", section="Matthew 4", chunk_order=7, text="alpha"),
            _hit(2, doc_key="nt", section=None, chunk_order=8, text="beta"),
        ]
        ctx = service._format_context(hits, [])
        assert "[1] Nt — Matthew 4" in ctx
        assert "alpha" in ctx
        # No section → label is just the title, no trailing dash.
        assert "[2] Nt\n" in ctx


@pytest.fixture
def fake_settings() -> MagicMock:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-5-mini"
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

        result = service.vector_search(tenant_id="t1", query="hello", limit=5, access_keys=[1, 2], tags=["tag"])
        assert result["total"] == 1
        assert result["hits"][0]["id"] == "chunk-1"

        mock_stores.embedder.embed.assert_called_once_with("hello")
        mock_stores.vector.search.assert_called_once()
        call = mock_stores.vector.search.call_args
        assert call.kwargs["tenant_id"] == "t1"
        assert call.kwargs["access_keys"] == [1, 2]
        assert call.kwargs["required_tags"] == ["tag"]


class TestVectorChatStream:
    def test_emits_sources_then_graph_then_done(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
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
            events = list(service.vector_chat_stream(tenant_id="t1", query="hello", use_knowledge_graph=True))

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
            events = list(service.vector_chat_stream(tenant_id="t1", query="hello", use_knowledge_graph=False))

        # Graph event still emitted but with empty list; graph store not queried
        graph_events = [e for e in events if e["type"] == "graph"]
        assert len(graph_events) == 1
        assert graph_events[0]["triplets"] == []
        mock_stores.graph.fuzzy_relationship_search.assert_not_called()

    def test_retrieval_error_emits_error_event(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        mock_stores.embedder.embed.side_effect = RuntimeError("embedding failed")
        with patch("brain_api.services.search_service.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
            events = list(service.vector_chat_stream(tenant_id="t1", query="hi"))

        assert events == [{"type": "error", "message": "Retrieval failed"}]
