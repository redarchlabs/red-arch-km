"""Tests for the IngestService with mocked stores."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from brain_api.services.ingest_service import IngestService, _centroid


class _FakeEmbedder:
    dimension = 4

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1 * (i + 1)] * 4 for i in range(len(texts))]


@pytest.fixture
def mock_stores() -> MagicMock:
    stores = MagicMock()
    stores.embedder = _FakeEmbedder()
    stores.vector = MagicMock()
    stores.graph = MagicMock()
    stores.summarizer = MagicMock()
    stores.summarizer.summarize_chunks.side_effect = lambda chunks: [f"summary-{i}" for i, _ in enumerate(chunks)]
    stores.summarizer.summarize_document.return_value = "final doc summary"
    stores.extractor = MagicMock()
    stores.extractor.extract.return_value = [("subject", "predicate", "object")]
    return stores


class TestCentroid:
    def test_empty_returns_empty(self) -> None:
        assert _centroid([]) == []

    def test_mean_of_identical_vectors(self) -> None:
        assert _centroid([[1.0, 2.0], [1.0, 2.0]]) == [1.0, 2.0]

    def test_mean_of_differing_vectors(self) -> None:
        assert _centroid([[1.0, 2.0], [3.0, 4.0]]) == [2.0, 3.0]


class TestIngestService:
    def test_empty_text_returns_zero_chunks(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="",
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )
        assert result["chunks"] == 0
        assert result["triplets"] == 0

    def test_ensure_collections_called(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )
        mock_stores.vector.ensure_collections.assert_called_once_with("t1")

    def test_chunk_summaries_stored_in_payload(self, mock_stores: MagicMock) -> None:
        """Chunk summaries must land in the chunk record payload — not discarded."""
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=["tag1"],
            access_keys=[42],
            use_knowledge_graph=False,
        )
        # First upsert_vectors call is chunks; second is the doc-level record.
        chunk_call = mock_stores.vector.upsert_vectors.call_args_list[0]
        records = chunk_call.args[1]
        assert all("summary" in r.payload for r in records)
        assert all(r.payload["summary"].startswith("summary-") for r in records)

    def test_document_summary_uses_hierarchical(self, mock_stores: MagicMock) -> None:
        """The service should call summarize_document, not the old joined-text path."""
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )
        mock_stores.summarizer.summarize_document.assert_called_once()

    def test_doc_summary_failure_falls_back_to_centroid(self, mock_stores: MagicMock) -> None:
        """Empty doc summary → doc vector is the centroid of chunk embeddings."""
        mock_stores.summarizer.summarize_document.return_value = ""
        service = IngestService(mock_stores)
        # Text long enough to span multiple chunks (chunk_size is 500 tokens), so the
        # centroid is a genuine mean of differing chunk embeddings rather than a single
        # chunk equal to itself. _FakeEmbedder.embed_batch returns a distinct vector per
        # chunk index, so the mean must differ from any individual chunk's embedding.
        text = " ".join(f"Sentence number {i} has some content." for i in range(200))
        service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text=text,
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )

        chunk_call = mock_stores.vector.upsert_vectors.call_args_list[0]
        chunk_vectors = [record.vector for record in chunk_call.args[1]]
        assert len(chunk_vectors) >= 2, "test requires >1 chunk for a meaningful centroid"

        doc_call = mock_stores.vector.upsert_vectors.call_args_list[1]
        doc_record = doc_call.args[1][0]
        # The doc vector is the centroid (mean) of the chunk embeddings...
        assert doc_record.vector == _centroid(chunk_vectors)
        # ...not merely the first chunk's embedding.
        assert doc_record.vector != chunk_vectors[0]

    def test_knowledge_graph_batched_insert(self, mock_stores: MagicMock) -> None:
        """All triplets are collected then inserted in one batch call."""
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=[],
            access_keys=[],
            use_knowledge_graph=True,
        )
        mock_stores.graph.insert_triplets.assert_called_once()
        assert result["triplets"] > 0

    def test_triplet_extraction_failure_does_not_halt_ingestion(self, mock_stores: MagicMock) -> None:
        mock_stores.extractor.extract.side_effect = RuntimeError("LLM failed")
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=[],
            access_keys=[],
            use_knowledge_graph=True,
        )
        assert result["chunks"] > 0
        assert result["triplets"] == 0
        mock_stores.graph.insert_triplets.assert_not_called()

    def test_triplet_insert_failure_returns_zero(self, mock_stores: MagicMock) -> None:
        """If the batched insert itself fails, ingestion still returns success."""
        mock_stores.graph.insert_triplets.side_effect = RuntimeError("Neo4j down")
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=[],
            access_keys=[],
            use_knowledge_graph=True,
        )
        assert result["chunks"] > 0
        assert result["triplets"] == 0

    def test_remove_document_calls_both_stores(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.remove_document("t1", "dk1")
        mock_stores.vector.delete_document.assert_called_once_with("t1", "dk1")
        mock_stores.graph.delete_by_document_key.assert_called_once_with("t1", "dk1")

    def test_init_tenant(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.init_tenant("t1")
        mock_stores.vector.ensure_collections.assert_called_once_with("t1")
        mock_stores.graph.initialize_tenant.assert_called_once_with("t1")
