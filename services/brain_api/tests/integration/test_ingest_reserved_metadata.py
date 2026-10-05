"""Regression: document metadata must not override reserved payload fields.

Runs the real ingest pipeline against a real Qdrant so the assertion is about
what retrieval actually filters on, not about a mocked payload dict.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from brain_api.services.ingest_service import IngestService
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore

pytestmark = pytest.mark.integration

_VECTOR = [0.1, 0.2, 0.3, 0.4]
_RESTRICTED_MASK = 42
_PUBLIC_MASK = 0


class _FakeEmbedder:
    dimension = 4

    def embed(self, text: str) -> list[float]:
        return list(_VECTOR)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [list(_VECTOR) for _ in texts]


def _service(vector_store: QdrantVectorStore) -> IngestService:
    stores = MagicMock()
    stores.vector = vector_store
    stores.embedder = _FakeEmbedder()
    stores.summarizer.summarize_chunks.side_effect = lambda chunks: ["s" for _ in chunks]
    stores.summarizer.summarize_document_hierarchy.return_value = ("doc summary", None)
    stores.settings.use_fact_engine = False
    return IngestService(stores)


def _ingest(service: IngestService, tenant: str, key: str, metadata: dict[str, object]) -> None:
    service.ingest_document(
        tenant_id=tenant,
        document_key=key,
        title="Restricted doc",
        text="Confidential salary bands for the engineering department.",
        tags=["folder:restricted"],
        access_keys=[_RESTRICTED_MASK],
        use_knowledge_graph=False,
        metadata=metadata,
    )


class TestReservedMetadataAgainstQdrant:
    def test_metadata_cannot_make_restricted_chunks_public(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        key = f"doc-{uuid.uuid4().hex[:8]}"
        _ingest(
            _service(vector_store),
            tenant,
            key,
            {"access_keys": [_PUBLIC_MASK], "tags": ["folder:public"], "author": "Ada"},
        )

        public_hits = vector_store.search(tenant, _VECTOR, limit=10, access_keys=[_PUBLIC_MASK])
        assert public_hits == []
        escaped_hits = vector_store.search(tenant, _VECTOR, limit=10, any_tags=["folder:public"])
        assert escaped_hits == []

        entitled_hits = vector_store.search(
            tenant, _VECTOR, limit=10, access_keys=[_RESTRICTED_MASK], any_tags=["folder:restricted"]
        )
        assert entitled_hits
        assert all(h.payload["document_key"] == key for h in entitled_hits)

        doc = vector_store.get_document_record(tenant, key)
        assert doc is not None

    def test_metadata_cannot_redirect_chunks_to_another_document_key(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        victim = f"victim-{uuid.uuid4().hex[:8]}"
        attacker = f"attacker-{uuid.uuid4().hex[:8]}"
        _ingest(_service(vector_store), tenant, attacker, {"document_key": victim, "tenant_id": "other"})

        assert vector_store.count_document_chunks(tenant, victim) == 0
        assert vector_store.document_exists(tenant, victim) is False
        assert vector_store.count_document_chunks(tenant, attacker) >= 1
        assert vector_store.document_exists(tenant, attacker) is True
