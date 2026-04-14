"""Tests for the IngestService with mocked stores."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from brain_api.services.ingest_service import IngestService


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
    stores.summarizer.summarize_chunks.return_value = ["summary 1", "summary 2", "summary 3"]
    stores.summarizer.create_document_summary.return_value = "doc summary"
    stores.extractor = MagicMock()
    stores.extractor.extract.return_value = [("subject", "predicate", "object")]
    return stores


class TestIngestService:
    def test_empty_text_returns_zero_chunks(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1", document_key="dk1", title="Doc",
            text="", tags=[], access_keys=[], use_knowledge_graph=False,
        )
        assert result["chunks"] == 0
        assert result["triplets"] == 0

    def test_ensure_collections_called(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1", document_key="dk1", title="Doc",
            text="Hello world. This is a test.", tags=[], access_keys=[],
            use_knowledge_graph=False,
        )
        mock_stores.vector.ensure_collections.assert_called_once_with("t1")

    def test_chunks_and_document_upserted(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1", document_key="dk1", title="Doc",
            text="Hello world. This is a test.", tags=["tag1"], access_keys=[42],
            use_knowledge_graph=False,
        )
        # Two upsert calls: one for chunks, one for document
        assert mock_stores.vector.upsert_vectors.call_count == 2
        chunk_call = mock_stores.vector.upsert_vectors.call_args_list[0]
        assert chunk_call.kwargs["collection_type"] == "chunks"

    def test_knowledge_graph_skipped(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1", document_key="dk1", title="Doc",
            text="Hello world. This is a test.", tags=[], access_keys=[],
            use_knowledge_graph=False,
        )
        mock_stores.graph.insert_triplets.assert_not_called()
        mock_stores.extractor.extract.assert_not_called()

    def test_knowledge_graph_extracts_and_stores(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1", document_key="dk1", title="Doc",
            text="Hello world. This is a test.", tags=[], access_keys=[],
            use_knowledge_graph=True,
        )
        mock_stores.extractor.extract.assert_called()
        mock_stores.graph.insert_triplets.assert_called()
        assert result["triplets"] > 0

    def test_triplet_failure_does_not_halt_ingestion(self, mock_stores: MagicMock) -> None:
        mock_stores.extractor.extract.side_effect = RuntimeError("LLM failed")
        service = IngestService(mock_stores)
        result = service.ingest_document(
            tenant_id="t1", document_key="dk1", title="Doc",
            text="Hello world. This is a test.", tags=[], access_keys=[],
            use_knowledge_graph=True,
        )
        # Chunks should still be stored even when triplet extraction fails
        assert result["chunks"] > 0
        assert result["triplets"] == 0

    def test_remove_document_calls_both_stores(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.remove_document("t1", "dk1")
        mock_stores.vector.delete_document.assert_called_once_with("t1", "dk1")
        mock_stores.graph.delete_by_document_key.assert_called_once_with("t1", "dk1")

    def test_remove_document_tolerates_vector_failure(self, mock_stores: MagicMock) -> None:
        mock_stores.vector.delete_document.side_effect = RuntimeError("Qdrant down")
        service = IngestService(mock_stores)
        service.remove_document("t1", "dk1")
        # Graph delete still runs
        mock_stores.graph.delete_by_document_key.assert_called_once_with("t1", "dk1")

    def test_init_tenant(self, mock_stores: MagicMock) -> None:
        service = IngestService(mock_stores)
        service.init_tenant("t1")
        mock_stores.vector.ensure_collections.assert_called_once_with("t1")
        mock_stores.graph.initialize_tenant.assert_called_once_with("t1")
