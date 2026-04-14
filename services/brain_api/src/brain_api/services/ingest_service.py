"""Document ingestion orchestration: chunk → embed → summarize → store (+ triplets)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from brain_sdk.chunking.chunker import chunk_text
from brain_sdk.vector_store.protocol import VectorRecord

from brain_api.stores import Stores

logger = logging.getLogger(__name__)

_CHUNK_SIZE_TOKENS = 500
_CHUNK_OVERLAP_TOKENS = 20


class IngestService:
    """Orchestrates the document ingestion pipeline."""

    def __init__(self, stores: Stores) -> None:
        self._stores = stores

    def ingest_document(
        self,
        *,
        tenant_id: str,
        document_key: str,
        title: str,
        text: str,
        tags: list[str],
        access_keys: list[int],
        use_knowledge_graph: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the full ingestion pipeline for a document.

        Returns a summary of what was ingested (chunk count, triplet count, etc.).
        """
        logger.info("Ingesting %s for tenant %s (%d chars)", document_key, tenant_id, len(text))

        # 1. Ensure per-tenant collections exist
        self._stores.vector.ensure_collections(tenant_id)

        # 2. Chunk the text
        chunks = chunk_text(text, desired_chunk_size=_CHUNK_SIZE_TOKENS, desired_overlap=_CHUNK_OVERLAP_TOKENS)
        if not chunks:
            logger.warning("No chunks produced from document %s", document_key)
            return {"document_key": document_key, "chunks": 0, "triplets": 0}

        # 3. Embed chunks in a batch
        chunk_embeddings = self._stores.embedder.embed_batch(chunks)

        # 4. Build chunk vector records
        doc_id = str(uuid.uuid4())
        chunk_records = [
            VectorRecord(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "text": chunk,
                    "chunk_order": idx,
                    "document_id": doc_id,
                    "document_key": document_key,
                    "document_title": title,
                    "tenant_id": tenant_id,
                    "tags": tags,
                    "access_keys": access_keys or [0],
                    "type": "chunk",
                    **(metadata or {}),
                },
            )
            for idx, (chunk, embedding) in enumerate(zip(chunks, chunk_embeddings, strict=True))
        ]

        self._stores.vector.upsert_vectors(tenant_id, chunk_records, collection_type="chunks")
        logger.info("Stored %d chunk vectors for %s", len(chunk_records), document_key)

        # 5. Build and store document-level record (summary + embedding)
        summary = self._build_document_summary(chunks)
        doc_vector = self._stores.embedder.embed(summary) if summary else chunk_embeddings[0]
        doc_record = VectorRecord(
            id=doc_id,
            vector=doc_vector,
            payload={
                "document_id": doc_id,
                "document_key": document_key,
                "document_title": title,
                "summary": summary,
                "tenant_id": tenant_id,
                "tags": tags,
                "access_keys": access_keys or [0],
                "type": "document",
                **(metadata or {}),
            },
        )
        self._stores.vector.upsert_vectors(tenant_id, [doc_record], collection_type="documents")

        # 6. Extract and store triplets (optional)
        triplet_count = 0
        if use_knowledge_graph:
            triplet_count = self._extract_and_store_triplets(
                tenant_id=tenant_id,
                document_key=document_key,
                chunks=chunks,
                tags=tags,
                access_keys=access_keys,
            )

        logger.info(
            "Ingest complete for %s: %d chunks, %d triplets",
            document_key, len(chunk_records), triplet_count,
        )
        return {
            "document_key": document_key,
            "document_id": doc_id,
            "chunks": len(chunk_records),
            "triplets": triplet_count,
        }

    def _build_document_summary(self, chunks: list[str]) -> str:
        """Create a document-level summary.

        For short documents (<3 chunks) we skip LLM summarization to save cost
        and just concatenate the chunks.
        """
        if len(chunks) < 3:
            return " ".join(chunks)[:2000]

        try:
            chunk_summaries = self._stores.summarizer.summarize_chunks(chunks)
            return self._stores.summarizer.create_document_summary(chunk_summaries)
        except Exception as e:
            logger.error("Summarization failed; falling back to truncation: %s", e)
            return " ".join(chunks)[:2000]

    def _extract_and_store_triplets(
        self,
        *,
        tenant_id: str,
        document_key: str,
        chunks: list[str],
        tags: list[str],
        access_keys: list[int],
    ) -> int:
        """Extract triplets from each chunk and insert into the graph store."""
        total = 0
        for chunk in chunks:
            try:
                triplets = self._stores.extractor.extract(chunk)
            except Exception as e:
                logger.error("Triplet extraction failed: %s", e)
                continue

            if not triplets:
                continue

            try:
                self._stores.graph.insert_triplets(
                    tenant_id=tenant_id,
                    triplets=triplets,
                    document_key=document_key,
                    tags=tags,
                    access_keys=access_keys,
                )
                total += len(triplets)
            except Exception as e:
                logger.error("Triplet insert failed: %s", e)
        return total

    def remove_document(self, tenant_id: str, document_key: str) -> None:
        """Remove a document from both vector and graph stores."""
        try:
            self._stores.vector.delete_document(tenant_id, document_key)
        except Exception as e:
            logger.error("Vector delete failed for %s: %s", document_key, e)

        try:
            self._stores.graph.delete_by_document_key(tenant_id, document_key)
        except Exception as e:
            logger.error("Graph delete failed for %s: %s", document_key, e)

    def update_metadata(
        self,
        *,
        tenant_id: str,
        document_key: str,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
        title: str | None = None,
    ) -> None:
        """Update tags/access_keys/title in both stores."""
        self._stores.vector.update_metadata(
            tenant_id=tenant_id,
            document_key=document_key,
            tags=tags,
            access_keys=access_keys,
            title=title,
        )
        self._stores.graph.update_metadata(
            tenant_id=tenant_id,
            document_key=document_key,
            tags=tags,
            access_keys=access_keys,
        )

    def init_tenant(self, tenant_id: str) -> None:
        """Initialize vector collections and graph schema for a new tenant."""
        self._stores.vector.ensure_collections(tenant_id)
        self._stores.graph.initialize_tenant(tenant_id)
