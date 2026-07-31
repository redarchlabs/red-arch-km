"""Tests for SearchService with mocked dependencies."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from brain_api.services.search_service import SearchService, _snippet
from brain_sdk.reranking.protocol import RerankResult
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
        with patch("brain_api.openai_client.OpenAI"):
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
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = [{"id": "c", "score": 0.5, "payload": {"text": "t", "document_key": "d"}}]
        sources = service._passage_sources(hits)
        assert sources[0]["section"] is None
        assert sources[0]["chunk_order"] is None


class TestFormatContext:
    def test_numbers_per_passage_with_section_label(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI"):
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
    settings.rerank_candidates = 30
    settings.chat_chunk_limit = 10
    return settings


@pytest.fixture
def mock_stores() -> MagicMock:
    stores = MagicMock()
    # Reranking off by default — every attribute of a MagicMock is truthy, so
    # without this the whole suite would exercise the rerank path against a mock.
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
    stores.graph.fuzzy_relationship_search.return_value = [
        {"subj": "Alice", "pred": "knows", "obj": "Bob"},
    ]
    return stores


class TestWarmUp:
    def test_exercises_every_read_path(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        """warm_up touches embedding + Qdrant + Neo4j + a tiny chat completion so
        the first real user query doesn't pay cold connection/TLS setup."""
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.warm_up()

        mock_stores.embedder.embed.assert_called_once()
        mock_stores.vector.search.assert_called_once()
        mock_stores.graph.fuzzy_relationship_search.assert_called_once()
        # A minimal chat completion primes the OpenAI connection pool.
        fake_openai.return_value.chat.completions.create.assert_called_once()

    def test_one_failing_path_does_not_stop_the_others(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        """Each probe is isolated: a cold path that errors must not prevent the
        remaining paths from warming, and warm_up must never raise."""
        mock_stores.vector.search.side_effect = RuntimeError("qdrant not ready")
        with patch("brain_api.openai_client.OpenAI") as fake_openai:
            service = SearchService(mock_stores, fake_settings)
            service.warm_up()  # must not raise

        # Graph + chat still ran despite the retrieval path failing.
        mock_stores.graph.fuzzy_relationship_search.assert_called_once()
        fake_openai.return_value.chat.completions.create.assert_called_once()


class TestVectorSearch:
    def test_returns_hits(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI"):
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


class TestChatChunkLimit:
    """How many ranked passages ground an answer.

    5 was too tight for an "each X" question: the passages answering "how many
    people can each ship handle" live in two documents, and five slots let the
    best-ranked one take them all.
    """

    @staticmethod
    def _limit_passed_to_search(stores: MagicMock) -> int:
        return int(stores.vector.search.call_args.kwargs["limit"])

    def test_chat_defaults_to_the_configured_limit(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        fake_settings.chat_chunk_limit = 10
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_chat(tenant_id="t1", query="q", use_knowledge_graph=False)

        assert self._limit_passed_to_search(mock_stores) == 10

    def test_stream_defaults_to_the_configured_limit(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        """The streaming path is the one the UI chat actually takes, so it must not
        drift from the non-streaming default."""
        fake_settings.chat_chunk_limit = 10
        with patch("brain_api.openai_client.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = iter([])
            service = SearchService(mock_stores, fake_settings)
            list(service.vector_chat_stream(tenant_id="t1", query="q", use_knowledge_graph=False))

        assert self._limit_passed_to_search(mock_stores) == 10

    def test_explicit_limit_overrides_the_setting(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        """Keeps the endpoint's chunk_limit usable for A/B-ing without a redeploy."""
        fake_settings.chat_chunk_limit = 10
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_chat(tenant_id="t1", query="q", chunk_limit=3, use_knowledge_graph=False)

        assert self._limit_passed_to_search(mock_stores) == 3


class TestVectorChatStream:
    def test_emits_sources_then_graph_then_done(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI") as mock_openai:
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
        with patch("brain_api.openai_client.OpenAI") as mock_openai:
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
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
            events = list(service.vector_chat_stream(tenant_id="t1", query="hi"))

        assert events == [{"type": "error", "message": "Retrieval failed"}]


class TestDocumentExpansion:
    """Dense top-k ranks the passage that *describes* a thing above the passage
    that *contains* it, so the winning document's siblings are read too."""

    @staticmethod
    def _sibling(number: int, *, doc_key: str, chunk_order: int, text: str) -> SearchResult:
        return SearchResult(
            id=f"sib-{number}",
            score=0.0,
            payload={
                "text": text,
                "document_key": doc_key,
                "document_title": doc_key.title(),
                "section": f"Section {chunk_order}",
                "chunk_order": chunk_order,
            },
        )

    def test_top_document_siblings_are_added_in_reading_order(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        mock_stores.vector.search.return_value = [
            SearchResult(
                id="chunk-a",
                score=0.73,
                payload={"text": "Six ships make up the fleet.", "document_key": "fleet", "chunk_order": 0},
            ),
        ]
        # Returned out of order to prove the store's ordering is relied upon, not luck.
        mock_stores.vector.list_document_chunks.return_value = [
            self._sibling(1, doc_key="fleet", chunk_order=1, text="Magellan, Cassini, Phoenix."),
            self._sibling(2, doc_key="fleet", chunk_order=2, text="Odyssey, Galileo, Falcon."),
        ]
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        result = service.vector_search(tenant_id="t1", query="name all six ships")

        assert [h["id"] for h in result["hits"]] == ["chunk-a", "sib-1", "sib-2"]
        assert result["total"] == 3
        # The names the ranked hit lacked are now in context.
        assert "Magellan" in result["hits"][1]["payload"]["text"]
        # Siblings are flagged and unscored — they were not vector matches.
        assert result["hits"][1]["expanded"] is True
        assert result["hits"][1]["score"] == 0.0
        assert "expanded" not in result["hits"][0]

    def test_expansion_reapplies_access_and_tag_filters(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        """Expansion must never widen visibility: the sibling fetch carries the
        caller's access keys and tag scope."""
        mock_stores.vector.list_document_chunks.return_value = []
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_search(
            tenant_id="t1",
            query="q",
            access_keys=[7],
            tags=["folder:hr"],
            folder_tags=["folder:hr", "folder:ops"],
        )
        kwargs = mock_stores.vector.list_document_chunks.call_args.kwargs
        assert kwargs["access_keys"] == [7]
        assert kwargs["required_tags"] == ["folder:hr"]
        assert kwargs["any_tags"] == ["folder:hr", "folder:ops"]
        assert kwargs["document_key"] == "key-1"

    def test_already_retrieved_chunks_are_not_duplicated(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        mock_stores.vector.list_document_chunks.return_value = [
            SearchResult(id="chunk-1", score=0.0, payload={"text": "Hello world.", "document_key": "key-1"}),
            self._sibling(9, doc_key="key-1", chunk_order=1, text="New material."),
        ]
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q")["hits"]
        assert [h["id"] for h in hits] == ["chunk-1", "sib-9"]

    def test_char_budget_caps_added_context(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        mock_stores.vector.list_document_chunks.return_value = [
            self._sibling(1, doc_key="key-1", chunk_order=1, text="x" * 5000),
            self._sibling(2, doc_key="key-1", chunk_order=2, text="y" * 5000),
        ]
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q")["hits"]
        # First sibling fits the 6000-char budget, the second would blow it.
        assert [h["id"] for h in hits] == ["chunk-1", "sib-1"]

    def test_only_the_top_document_is_expanded(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        mock_stores.vector.search.return_value = [
            SearchResult(id="a", score=0.9, payload={"text": "a", "document_key": "first", "chunk_order": 0}),
            SearchResult(id="b", score=0.8, payload={"text": "b", "document_key": "second", "chunk_order": 0}),
        ]
        mock_stores.vector.list_document_chunks.return_value = []
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_search(tenant_id="t1", query="q")
        assert mock_stores.vector.list_document_chunks.call_count == 1
        assert mock_stores.vector.list_document_chunks.call_args.kwargs["document_key"] == "first"

    def test_flag_off_returns_ranked_hits_only(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q", expand_documents=False)["hits"]
        assert [h["id"] for h in hits] == ["chunk-1"]
        mock_stores.vector.list_document_chunks.assert_not_called()

    def test_failed_expansion_degrades_to_ranked_hits(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        mock_stores.vector.list_document_chunks.side_effect = RuntimeError("qdrant down")
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q")["hits"]
        assert [h["id"] for h in hits] == ["chunk-1"]

    def test_expanded_passages_are_citable_sources(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        """Sources and context blocks both enumerate the same list, so an expanded
        passage gets its own citation number rather than shifting the others."""
        mock_stores.vector.list_document_chunks.return_value = [
            self._sibling(1, doc_key="key-1", chunk_order=1, text="The six names."),
        ]
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q")["hits"]
        sources = service._passage_sources(hits)
        assert [s["number"] for s in sources] == [1, 2]
        assert sources[1]["section"] == "Section 1"
        assert "[2] Key-1 — Section 1" in service._format_context(hits, [])


class TestRerank:
    """Cross-encoder re-scoring of the dense shortlist.

    Modelled on the real failure: asked "how many people can each ship handle",
    dense search put a booking FAQ about crew limits first and left the fleet
    document's per-ship crew complements outside the top 5 entirely.
    """

    @pytest.fixture(autouse=True)
    def _reranker(self, mock_stores: MagicMock) -> None:
        """Opt this class back into reranking — the shared fixture turns it off."""
        mock_stores.reranker = MagicMock()

    @staticmethod
    def _result(chunk_id: str, score: float, *, doc_key: str, text: str) -> SearchResult:
        return SearchResult(
            id=chunk_id,
            score=score,
            payload={"text": text, "document_key": doc_key, "document_title": doc_key.title()},
        )

    @pytest.fixture
    def dense_hits(self) -> list[SearchResult]:
        return [
            self._result("faq", 0.724, doc_key="booking", text="Can we bring more than the crew limit?"),
            self._result("intro", 0.717, doc_key="fleet", text="Six ships make up the current fleet."),
            self._result("cassini", 0.667, doc_key="fleet", text="USS Cassini — Standard Crew Complement: 450"),
        ]

    def test_reranked_order_replaces_dense_order(
        self, mock_stores: MagicMock, fake_settings: MagicMock, dense_hits: list[SearchResult]
    ) -> None:
        mock_stores.vector.search.return_value = dense_hits
        # The cross-encoder promotes the passage that actually answers the question.
        mock_stores.reranker.rerank.return_value = [
            RerankResult(index=2, score=0.98),
            RerankResult(index=1, score=0.41),
        ]
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="how many people per ship", limit=2, expand_documents=False)[
            "hits"
        ]

        assert [h["id"] for h in hits] == ["cassini", "intro"]
        assert hits[0]["score"] == 0.98
        # The dense score is kept alongside so the two rankings stay comparable.
        assert hits[0]["dense_score"] == 0.667

    def test_shortlist_is_wider_than_the_requested_limit(
        self, mock_stores: MagicMock, fake_settings: MagicMock, dense_hits: list[SearchResult]
    ) -> None:
        """Reranking can only promote a passage that was retrieved: the answering
        chunk sat below the old top-5, so the candidate pool must be wider."""
        mock_stores.vector.search.return_value = dense_hits
        mock_stores.reranker.rerank.return_value = [RerankResult(index=2, score=0.98)]
        fake_settings.rerank_candidates = 30
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_search(tenant_id="t1", query="q", limit=5, expand_documents=False)

        assert mock_stores.vector.search.call_args.kwargs["limit"] == 30
        assert mock_stores.reranker.rerank.call_args.kwargs["top_n"] == 5

    def test_limit_wins_when_it_exceeds_the_candidate_pool(
        self, mock_stores: MagicMock, fake_settings: MagicMock
    ) -> None:
        mock_stores.reranker.rerank.return_value = [RerankResult(index=0, score=0.9)]
        fake_settings.rerank_candidates = 3
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_search(tenant_id="t1", query="q", limit=10, expand_documents=False)

        assert mock_stores.vector.search.call_args.kwargs["limit"] == 10

    def test_expansion_follows_the_reranked_leader(
        self, mock_stores: MagicMock, fake_settings: MagicMock, dense_hits: list[SearchResult]
    ) -> None:
        """The whole point of the fix: sibling expansion must read the document
        the reranker promoted, not the one dense search happened to rank first."""
        mock_stores.vector.search.return_value = dense_hits
        mock_stores.vector.list_document_chunks.return_value = []
        mock_stores.reranker.rerank.return_value = [RerankResult(index=2, score=0.98)]
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        service.vector_search(tenant_id="t1", query="q", limit=1)

        assert mock_stores.vector.list_document_chunks.call_args.kwargs["document_key"] == "fleet"

    def test_rerank_failure_degrades_to_dense_order(
        self, mock_stores: MagicMock, fake_settings: MagicMock, dense_hits: list[SearchResult]
    ) -> None:
        mock_stores.vector.search.return_value = dense_hits
        mock_stores.reranker.rerank.side_effect = RuntimeError("rerank server down")
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q", limit=2, expand_documents=False)["hits"]

        # Dense top-`limit`, exactly as before reranking existed — and truncated,
        # so a wider shortlist never leaks into the prompt on the failure path.
        assert [h["id"] for h in hits] == ["faq", "intro"]

    def test_empty_rerank_result_degrades_to_dense_order(
        self, mock_stores: MagicMock, fake_settings: MagicMock, dense_hits: list[SearchResult]
    ) -> None:
        """An empty list would answer from no context at all."""
        mock_stores.vector.search.return_value = dense_hits
        mock_stores.reranker.rerank.return_value = []
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q", limit=2, expand_documents=False)["hits"]

        assert [h["id"] for h in hits] == ["faq", "intro"]

    def test_no_reranker_leaves_retrieval_untouched(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        mock_stores.reranker = None
        fake_settings.rerank_candidates = 30
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q", limit=5, expand_documents=False)["hits"]

        # No over-fetch, no dense_score, no behaviour change when unconfigured.
        assert mock_stores.vector.search.call_args.kwargs["limit"] == 5
        assert "dense_score" not in hits[0]

    def test_no_candidates_skips_the_reranker(self, mock_stores: MagicMock, fake_settings: MagicMock) -> None:
        mock_stores.vector.search.return_value = []
        with patch("brain_api.openai_client.OpenAI"):
            service = SearchService(mock_stores, fake_settings)
        hits = service.vector_search(tenant_id="t1", query="q", expand_documents=False)["hits"]

        assert hits == []
        mock_stores.reranker.rerank.assert_not_called()
