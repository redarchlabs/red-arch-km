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
    # ingest now consumes the hierarchical variant, which returns (summary, tree).
    stores.summarizer.summarize_document_hierarchy.return_value = (
        "final doc summary",
        {"summary": "final doc summary", "children": [{"summary": "summary-0", "children": []}]},
    )
    stores.extractor = MagicMock()
    stores.extractor.extract.return_value = [("subject", "predicate", "object")]
    # Default: fact engine off → the legacy triplet path is exercised here.
    stores.settings.use_fact_engine = False
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

    def test_section_stored_in_chunk_payload(self, mock_stores: MagicMock) -> None:
        """Each chunk payload carries its heading path in `section`.

        Prose with no headings must still carry the key (as None) so retrieval
        can rely on it being present.
        """
        service = IngestService(mock_stores)
        service.ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="# Overview\nHello world. This is a test.",
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )
        chunk_call = mock_stores.vector.upsert_vectors.call_args_list[0]
        records = chunk_call.args[1]
        assert all("section" in r.payload for r in records)
        assert all(r.payload["section"] == "Overview" for r in records)

    def test_section_is_none_for_unstructured_text(self, mock_stores: MagicMock) -> None:
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
        chunk_call = mock_stores.vector.upsert_vectors.call_args_list[0]
        records = chunk_call.args[1]
        assert all(r.payload["section"] is None for r in records)

    def test_document_summary_uses_hierarchical(self, mock_stores: MagicMock) -> None:
        """The service should call the hierarchical variant that also yields a tree."""
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
        mock_stores.summarizer.summarize_document_hierarchy.assert_called_once()

    def test_summary_tree_stored_in_doc_payload(self, mock_stores: MagicMock) -> None:
        """The hierarchical tree must be persisted on the doc-level record."""
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
        doc_call = mock_stores.vector.upsert_vectors.call_args_list[1]
        doc_record = doc_call.args[1][0]
        assert doc_record.payload["summary"] == "final doc summary"
        tree = doc_record.payload["summary_tree"]
        assert tree is not None
        assert tree["summary"] == "final doc summary"
        assert tree["children"][0]["summary"] == "summary-0"

    def test_summary_failure_synthesizes_fallback_tree(self, mock_stores: MagicMock) -> None:
        """If the hierarchical summariser raises, a flat summary + 2-level tree is stored."""
        mock_stores.summarizer.summarize_document_hierarchy.side_effect = RuntimeError("LLM down")
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
        doc_call = mock_stores.vector.upsert_vectors.call_args_list[1]
        doc_record = doc_call.args[1][0]
        tree = doc_record.payload["summary_tree"]
        assert tree is not None
        # Root is the flat summary; every leaf is a chunk summary with no children.
        assert all(child["children"] == [] for child in tree["children"])
        assert doc_record.payload["summary"] == tree["summary"]

    def test_doc_summary_failure_falls_back_to_centroid(self, mock_stores: MagicMock) -> None:
        """Empty doc summary → doc vector is the centroid of chunk embeddings."""
        mock_stores.summarizer.summarize_document_hierarchy.return_value = ("", None)
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


class TestNavigationalChunkFiltering:
    """Link-only sections are dropped before embedding: they consume retrieval
    slots on every corpus question and contain no answer."""

    @staticmethod
    def _indexed_texts(mock_stores: MagicMock) -> list[str]:
        """Chunk texts actually upserted into the chunks collection."""
        for call in mock_stores.vector.upsert_vectors.call_args_list:
            if call.kwargs.get("collection_type", "chunks") == "chunks":
                return [r.payload["text"] for r in call.kwargs.get("records", call.args[1])]
        return []

    def test_related_knowledge_section_is_not_indexed(self, mock_stores: MagicMock) -> None:
        text = (
            "# The Ship Fleet\n\nSix ships make up the current fleet.\n\n"
            "## Related knowledge\n\n- [Overview](10-overview.md)\n- [Staff](12-staff.md)\n"
        )
        IngestService(mock_stores).ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Fleet",
            text=text,
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )
        indexed = self._indexed_texts(mock_stores)
        assert any("Six ships" in t for t in indexed)
        assert not any("Related knowledge" in t for t in indexed)

    def test_document_that_is_only_links_is_still_indexed(self, mock_stores: MagicMock) -> None:
        """A pure link index must stay searchable — dropping every chunk would
        make the document invisible, which is worse than the noise it adds."""
        text = "# Index\n\n- [Alpha](a.md)\n- [Beta](b.md)\n- [Gamma](c.md)\n"
        result = IngestService(mock_stores).ingest_document(
            tenant_id="t1",
            document_key="dk2",
            title="Index",
            text=text,
            tags=[],
            access_keys=[],
            use_knowledge_graph=False,
        )
        assert result["chunks"] >= 1
        assert self._indexed_texts(mock_stores)


class TestReservedMetadata:
    """Caller metadata rides along in every chunk/document payload, but it must
    never override the fields retrieval filters and scopes on. A writer posting
    ``{"access_keys": [0]}`` would otherwise make restricted content public."""

    _HOSTILE = {
        "access_keys": [0],
        "tags": ["folder:someone-else"],
        "document_key": "other-doc",
        "document_id": "other-id",
        "tenant_id": "other-tenant",
        "type": "document",
        "text": "replaced",
        "summary": "replaced",
        "section": "replaced",
        "chunk_order": 999,
        "document_title": "replaced",
        "summary_tree": {"summary": "replaced"},
        "source": "kept",
    }

    def _ingest(self, mock_stores: MagicMock) -> None:
        IngestService(mock_stores).ingest_document(
            tenant_id="t1",
            document_key="dk1",
            title="Doc",
            text="Hello world. This is a test.",
            tags=["folder:f1"],
            access_keys=[42],
            use_knowledge_graph=False,
            metadata=dict(self._HOSTILE),
        )

    def test_chunk_payload_keeps_reserved_fields(self, mock_stores: MagicMock) -> None:
        self._ingest(mock_stores)
        for record in mock_stores.vector.upsert_vectors.call_args_list[0].args[1]:
            p = record.payload
            assert p["access_keys"] == [42]
            assert p["tags"] == ["folder:f1"]
            assert p["document_key"] == "dk1"
            assert p["tenant_id"] == "t1"
            assert p["type"] == "chunk"
            assert p["text"] != "replaced"
            assert p["chunk_order"] != 999
            assert p["document_title"] == "Doc"
            assert p["source"] == "kept"  # non-reserved metadata still lands

    def test_document_payload_keeps_reserved_fields(self, mock_stores: MagicMock) -> None:
        self._ingest(mock_stores)
        p = mock_stores.vector.upsert_vectors.call_args_list[1].args[1][0].payload
        assert p["access_keys"] == [42]
        assert p["tags"] == ["folder:f1"]
        assert p["document_key"] == "dk1"
        assert p["tenant_id"] == "t1"
        assert p["type"] == "document"
        assert p["summary"] == "final doc summary"
        assert p["summary_tree"] != {"summary": "replaced"}
        assert p["source"] == "kept"

    def test_reserved_key_set_is_exported(self) -> None:
        from brain_api.services.ingest_service import RESERVED_INGEST_METADATA_KEYS

        assert {"access_keys", "tags", "document_key", "tenant_id", "type"} <= RESERVED_INGEST_METADATA_KEYS


class TestUpdateMetadataReachesFacts:
    """A folder move or permission change must re-mask the document's facts too,
    or facts from a public→restricted move stay public — and must move its folder
    tags, which folder-limited graph search tests on the source Document node."""

    def test_claim_masks_updated_when_fact_engine_on(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        IngestService(mock_stores).update_metadata(tenant_id="t1", document_key="dk1", access_keys=[7])
        mock_stores.fact_store.update_document_metadata.assert_called_once_with("t1", "dk1", tags=None, access_keys=[7])

    def test_folder_move_retags_the_document_node(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        IngestService(mock_stores).update_metadata(
            tenant_id="t1", document_key="dk1", tags=["folder:new"], access_keys=[7]
        )
        mock_stores.fact_store.update_document_metadata.assert_called_once_with(
            "t1", "dk1", tags=["folder:new"], access_keys=[7]
        )

    def test_tags_only_change_reaches_facts(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        IngestService(mock_stores).update_metadata(tenant_id="t1", document_key="dk1", tags=["folder:new"])
        mock_stores.fact_store.update_document_metadata.assert_called_once_with(
            "t1", "dk1", tags=["folder:new"], access_keys=None
        )

    def test_title_only_change_leaves_facts_alone(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        IngestService(mock_stores).update_metadata(tenant_id="t1", document_key="dk1", title="New")
        mock_stores.fact_store.update_document_metadata.assert_not_called()

    def test_fact_engine_off_leaves_facts_alone(self, mock_stores: MagicMock) -> None:
        IngestService(mock_stores).update_metadata(tenant_id="t1", document_key="dk1", tags=["folder:new"])
        mock_stores.fact_store.update_document_metadata.assert_not_called()


class TestBackfillDocumentGraphMetadata:
    """Document nodes written before they carried tags have none, so folder-limited
    graph search excludes their claims (fails closed) until this copies each
    document's current tags and masks over from its first chunk's payload (the
    vector-store copy that metadata updates keep current)."""

    @staticmethod
    def _records(*payloads: dict) -> list[MagicMock]:
        return [MagicMock(payload=p) for p in payloads]

    def test_copies_tags_and_masks_from_the_vector_store(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        mock_stores.vector.iter_document_heads.return_value = self._records(
            {"document_key": "dk1", "tags": ["folder:a", "policy"], "access_keys": [5, 6]},
            {"document_key": "dk2", "tags": ["folder:b"], "access_keys": [0]},
            {"document_key": "dk3", "access_keys": []},
        )
        mock_stores.fact_store.update_document_metadata.return_value = 1

        result = IngestService(mock_stores).backfill_document_graph_metadata("t1")

        calls = mock_stores.fact_store.update_document_metadata.call_args_list
        assert [c.args for c in calls] == [("t1", "dk1"), ("t1", "dk2"), ("t1", "dk3")]
        assert calls[0].kwargs == {"tags": ["folder:a", "policy"], "access_keys": [5, 6]}
        # Ingest stores a public document's chunks as the [0] sentinel; the fact
        # graph's public form is the empty list.
        assert calls[1].kwargs == {"tags": ["folder:b"], "access_keys": []}
        assert calls[2].kwargs == {"tags": [], "access_keys": []}
        assert result == {"documents": 3, "claims_recomputed": 3, "skipped": 0}

    def test_each_document_is_written_once(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        mock_stores.vector.iter_document_heads.return_value = self._records(
            {"document_key": "dk1", "tags": ["folder:a"], "access_keys": [5]},
            {"document_key": "dk1", "tags": ["folder:a"], "access_keys": [5]},
        )
        mock_stores.fact_store.update_document_metadata.return_value = 0
        assert IngestService(mock_stores).backfill_document_graph_metadata("t1")["documents"] == 1
        mock_stores.fact_store.update_document_metadata.assert_called_once()

    def test_records_without_a_document_key_are_skipped_and_counted(self, mock_stores: MagicMock) -> None:
        mock_stores.settings.use_fact_engine = True
        mock_stores.vector.iter_document_heads.return_value = self._records({"tags": ["folder:a"]})
        result = IngestService(mock_stores).backfill_document_graph_metadata("t1")
        mock_stores.fact_store.update_document_metadata.assert_not_called()
        assert result["documents"] == 0
        assert result["skipped"] == 1

    def test_documents_without_a_first_chunk_are_skipped_and_counted(self, mock_stores: MagicMock) -> None:
        """A document whose chunk 0 is missing has no payload to copy from: it is
        left alone, but counted, so the operator knows the graph is still behind."""
        mock_stores.settings.use_fact_engine = True
        mock_stores.vector.iter_document_heads.return_value = self._records(
            {"document_key": "dk1", "tags": ["folder:a"], "access_keys": [5]}
        )
        mock_stores.vector.iter_document_keys.return_value = iter(["dk1", "dk2", "dk3"])
        mock_stores.fact_store.update_document_metadata.return_value = 0

        result = IngestService(mock_stores).backfill_document_graph_metadata("t1")

        assert result == {"documents": 1, "claims_recomputed": 0, "skipped": 2}
        mock_stores.fact_store.update_document_metadata.assert_called_once()

    def test_fact_engine_off_is_a_no_op(self, mock_stores: MagicMock) -> None:
        result = IngestService(mock_stores).backfill_document_graph_metadata("t1")
        mock_stores.vector.iter_document_heads.assert_not_called()
        assert result == {"documents": 0, "claims_recomputed": 0, "skipped": 0}
