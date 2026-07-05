"""Integration tests for QdrantVectorStore against a real Qdrant container."""

from __future__ import annotations

import uuid

import pytest
from brain_sdk.vector_store.protocol import VectorRecord
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore

pytestmark = pytest.mark.integration


def _make_records(tenant: str, doc_key: str, count: int) -> list[VectorRecord]:
    return [
        VectorRecord(
            id=str(uuid.uuid4()),
            vector=[0.1 * i, 0.2 * i, 0.3 * i, 0.4 * i],
            payload={
                "text": f"chunk {i}",
                "chunk_order": i,
                "document_key": doc_key,
                "document_title": "Doc",
                "tenant_id": tenant,
                "tags": [],
                "access_keys": [0],
                "type": "chunk",
            },
        )
        for i in range(count)
    ]


class TestQdrantStoreIntegration:
    def test_ensure_collections_creates_tenant_collections(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)
        # Idempotent — calling again should not raise
        vector_store.ensure_collections(tenant)

    def test_upsert_and_search(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        doc_key = f"doc-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)

        records = _make_records(tenant, doc_key, count=3)
        vector_store.upsert_vectors(tenant, records)

        # Search with a vector close to chunk 2
        results = vector_store.search(
            tenant_id=tenant,
            query_vector=[0.2, 0.4, 0.6, 0.8],
            limit=5,
        )
        assert len(results) > 0
        assert all(r.payload.get("document_key") == doc_key for r in results)

    def test_document_exists_round_trip(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        doc_key = f"doc-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)

        assert vector_store.document_exists(tenant, doc_key) is False

        # Seed a document-level record (the doc collection is what exists checks)
        doc_record = VectorRecord(
            id=str(uuid.uuid4()),
            vector=[0.5, 0.5, 0.5, 0.5],
            payload={"document_key": doc_key, "tenant_id": tenant, "type": "document"},
        )
        vector_store.upsert_vectors(tenant, [doc_record], collection_type="documents")

        assert vector_store.document_exists(tenant, doc_key) is True

    def test_delete_document_removes_all_chunks(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        doc_key = f"doc-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)

        vector_store.upsert_vectors(tenant, _make_records(tenant, doc_key, count=3))
        doc_record = VectorRecord(
            id=str(uuid.uuid4()),
            vector=[0.1, 0.2, 0.3, 0.4],
            payload={"document_key": doc_key, "tenant_id": tenant, "type": "document"},
        )
        vector_store.upsert_vectors(tenant, [doc_record], collection_type="documents")

        assert vector_store.document_exists(tenant, doc_key) is True

        vector_store.delete_document(tenant, doc_key)

        assert vector_store.document_exists(tenant, doc_key) is False
        chunks = vector_store.get_document_chunks(tenant, doc_key)
        assert chunks == []

    def test_tenant_isolation(self, vector_store: QdrantVectorStore) -> None:
        """Two tenants with the same document_key get isolated collections."""
        tenant_a = f"ta-{uuid.uuid4().hex[:8]}"
        tenant_b = f"tb-{uuid.uuid4().hex[:8]}"
        doc_key = "shared-key"

        vector_store.ensure_collections(tenant_a)
        vector_store.ensure_collections(tenant_b)

        vector_store.upsert_vectors(tenant_a, _make_records(tenant_a, doc_key, 2))
        vector_store.upsert_vectors(tenant_b, _make_records(tenant_b, doc_key, 1))

        chunks_a = vector_store.get_document_chunks(tenant_a, doc_key)
        chunks_b = vector_store.get_document_chunks(tenant_b, doc_key)

        assert len(chunks_a) == 2
        assert len(chunks_b) == 1
        assert all(c.payload["tenant_id"] == tenant_a for c in chunks_a)
        assert all(c.payload["tenant_id"] == tenant_b for c in chunks_b)

    def test_delete_tenant_removes_both_collections(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        doc_key = f"doc-{uuid.uuid4().hex[:8]}"

        vector_store.ensure_collections(tenant)
        vector_store.upsert_vectors(tenant, _make_records(tenant, doc_key, 2))
        doc_record = VectorRecord(
            id=str(uuid.uuid4()),
            vector=[0.1, 0.2, 0.3, 0.4],
            payload={"document_key": doc_key, "tenant_id": tenant, "type": "document"},
        )
        vector_store.upsert_vectors(tenant, [doc_record], collection_type="documents")
        assert vector_store.document_exists(tenant, doc_key) is True

        vector_store.delete_tenant(tenant)
        # Subsequent existence check on the missing collection returns False
        # rather than raising.
        assert vector_store.document_exists(tenant, doc_key) is False

    def test_delete_tenant_idempotent(self, vector_store: QdrantVectorStore) -> None:
        """Deleting a tenant that doesn't exist must not raise, and a second
        delete on an already-deleted tenant must also be a no-op."""
        tenant = f"ghost-{uuid.uuid4().hex[:8]}"
        vector_store.delete_tenant(tenant)
        vector_store.delete_tenant(tenant)

    def test_update_metadata(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        doc_key = f"doc-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)
        vector_store.upsert_vectors(tenant, _make_records(tenant, doc_key, 2))

        vector_store.update_metadata(tenant, doc_key, tags=["updated"], access_keys=[42], title="New Title")

        chunks = vector_store.get_document_chunks(tenant, doc_key)
        assert all(c.payload.get("tags") == ["updated"] for c in chunks)
        assert all(c.payload.get("access_keys") == [42] for c in chunks)
        assert all(c.payload.get("document_title") == "New Title" for c in chunks)

    def test_chunk_pagination_and_count(self, vector_store: QdrantVectorStore) -> None:
        """Chunks page by chunk_order window so large docs load incrementally."""
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        doc_key = f"doc-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)
        vector_store.upsert_vectors(tenant, _make_records(tenant, doc_key, 5))

        # Total is independent of any page window.
        assert vector_store.count_document_chunks(tenant, doc_key) == 5

        page1 = vector_store.get_document_chunks(tenant, doc_key, offset=0, limit=2)
        page2 = vector_store.get_document_chunks(tenant, doc_key, offset=2, limit=2)
        page3 = vector_store.get_document_chunks(tenant, doc_key, offset=4, limit=2)

        assert [int(c.payload["chunk_order"]) for c in page1] == [0, 1]
        assert [int(c.payload["chunk_order"]) for c in page2] == [2, 3]
        assert [int(c.payload["chunk_order"]) for c in page3] == [4]  # last, partial page

        # An offset past the end yields nothing (loop terminator for the reader).
        assert vector_store.get_document_chunks(tenant, doc_key, offset=10, limit=2) == []

    def test_count_missing_collection_is_zero(self, vector_store: QdrantVectorStore) -> None:
        """Counting a document whose collection doesn't exist yet returns 0."""
        tenant = f"ghost-{uuid.uuid4().hex[:8]}"
        assert vector_store.count_document_chunks(tenant, "nope") == 0
