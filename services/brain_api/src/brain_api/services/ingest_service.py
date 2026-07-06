"""Document ingestion orchestration.

Pipeline (all LLM/I-O calls parallelised where safe):

    chunk text ─┬─▶ parallel embed batch ─┬─▶ chunk records (text + summary)
                └─▶ parallel chunk summaries ─▶ hierarchical doc summary
                                                      │
                                                      └─▶ doc-level vector

    parallel triplet extraction per chunk ─▶ single UNWIND insert into Neo4j

Key guarantees:
- Chunk-level summaries are stored alongside raw chunk text so retrieval
  can surface them without recomputing.
- Document summary is hierarchical (groups of chunk summaries → group
  summaries → … → single summary) — never over-stuffs one LLM prompt.
- If document summarisation returns empty, the doc-level vector falls
  back to the centroid of chunk embeddings rather than the first chunk.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
import uuid
from typing import Any

from brain_sdk.chunking.chunker import chunk_text
from brain_sdk.facts.pipeline import Chunk
from brain_sdk.summarization.chunk_summarizer import SummaryNode
from brain_sdk.vector_store.protocol import VectorRecord
from shared_config import get_tracer

from brain_api.observability import get_metrics
from brain_api.stores import Stores

logger = logging.getLogger(__name__)
_tracer = get_tracer("brain_api.ingest")

_CHUNK_SIZE_TOKENS = 500
_CHUNK_OVERLAP_TOKENS = 20
_TRIPLET_WORKERS = 8


def _centroid(vectors: list[list[float]]) -> list[float]:
    """Arithmetic mean across a list of equal-length vectors.

    Raises ValueError if vectors have inconsistent dimensions — silently
    averaging mismatched vectors would produce a wrong centroid and a
    broken document-level search signal.
    """
    if not vectors:
        return []
    dim = len(vectors[0])
    if dim == 0:
        return []
    for idx, v in enumerate(vectors):
        if len(v) != dim:
            msg = f"centroid input dim mismatch: vectors[0]={dim} vs vectors[{idx}]={len(v)}"
            raise ValueError(msg)
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


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
        """Run the full ingestion pipeline for a document."""
        metrics = get_metrics()
        start = time.perf_counter()

        with _tracer.start_as_current_span(
            "ingest_document",
            attributes={
                "tenant_id": tenant_id,
                "document_key": document_key,
                "text_length": len(text),
            },
        ):
            logger.info(
                "Ingesting document",
                extra={
                    "document_key": document_key,
                    "tenant_id": tenant_id,
                    "text_length": len(text),
                },
            )

            with _tracer.start_as_current_span("ensure_collections"):
                self._stores.vector.ensure_collections(tenant_id)

            with _tracer.start_as_current_span("chunk_text") as span:
                chunks = chunk_text(
                    text,
                    desired_chunk_size=_CHUNK_SIZE_TOKENS,
                    desired_overlap=_CHUNK_OVERLAP_TOKENS,
                )
                span.set_attribute("chunk_count", len(chunks))

            if not chunks:
                logger.warning("No chunks produced from document %s", document_key)
                return {"document_key": document_key, "chunks": 0, "triplets": 0}

            # Embedding and summarisation are independent and network-bound,
            # so we run them concurrently.
            with (
                _tracer.start_as_current_span("embed_and_summarize") as span,
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as exe,
            ):
                embed_future = exe.submit(self._stores.embedder.embed_batch, chunks)
                summaries_future = exe.submit(self._stores.summarizer.summarize_chunks, chunks)
                chunk_embeddings = embed_future.result()
                chunk_summaries = summaries_future.result()
                span.set_attribute("batch_size", len(chunks))

            doc_id = str(uuid.uuid4())
            chunk_records = [
                VectorRecord(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={
                        "text": chunk,
                        "summary": summary,
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
                for idx, (chunk, embedding, summary) in enumerate(
                    zip(chunks, chunk_embeddings, chunk_summaries, strict=True)
                )
            ]

            with _tracer.start_as_current_span("upsert_chunks"):
                self._stores.vector.upsert_vectors(tenant_id, chunk_records, collection_type="chunks")

            with _tracer.start_as_current_span("document_summary"):
                doc_summary, summary_tree = self._safe_document_summary(chunk_summaries)
                doc_vector = self._choose_document_vector(doc_summary, chunk_embeddings)

            doc_record = VectorRecord(
                id=doc_id,
                vector=doc_vector,
                payload={
                    "document_id": doc_id,
                    "document_key": document_key,
                    "document_title": title,
                    "summary": doc_summary,
                    # Persist the hierarchical summary tree (nested {summary,
                    # children}) so the UI can render an expandable view rather
                    # than only the final rolled-up string.
                    "summary_tree": summary_tree,
                    "tenant_id": tenant_id,
                    "tags": tags,
                    "access_keys": access_keys or [0],
                    "type": "document",
                    **(metadata or {}),
                },
            )
            self._stores.vector.upsert_vectors(tenant_id, [doc_record], collection_type="documents")

            triplet_count = 0
            if use_knowledge_graph and not self._stores.settings.use_fact_engine:
                with _tracer.start_as_current_span("extract_and_store_triplets") as span:
                    triplet_count = self._extract_and_store_triplets(
                        tenant_id=tenant_id,
                        document_key=document_key,
                        chunks=chunks,
                        tags=tags,
                        access_keys=access_keys,
                    )
                    span.set_attribute("triplet_count", triplet_count)

            # New reified-claim fact engine (replaces the flat triplet path when
            # enabled). Idempotent: the pipeline purges the document's prior
            # claims before re-inserting.
            if use_knowledge_graph and self._stores.settings.use_fact_engine:
                with _tracer.start_as_current_span("extract_and_store_facts") as span:
                    facts = self._extract_and_store_facts(
                        tenant_id=tenant_id,
                        document_key=document_key,
                        chunks=chunks,
                        tags=tags,
                        access_keys=access_keys,
                    )
                    triplet_count = facts.get("claims_extracted", 0)
                    span.set_attribute("claim_count", triplet_count)

            duration_ms = (time.perf_counter() - start) * 1000
            metrics.chunks_ingested.add(len(chunk_records), {"tenant_id": tenant_id})
            metrics.triplets_ingested.add(triplet_count, {"tenant_id": tenant_id})
            metrics.ingest_duration_ms.record(duration_ms, {"tenant_id": tenant_id})

            logger.info(
                "Ingest complete",
                extra={
                    "document_key": document_key,
                    "chunks": len(chunk_records),
                    "triplets": triplet_count,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            return {
                "document_key": document_key,
                "document_id": doc_id,
                "chunks": len(chunk_records),
                "triplets": triplet_count,
            }

    def _safe_document_summary(self, chunk_summaries: list[str]) -> tuple[str, SummaryNode | None]:
        """Hierarchical summary + tree, with a safe fallback if the pipeline fails.

        Returns ``(summary, tree)`` where ``tree`` is the nested
        ``{"summary", "children"}`` structure the summariser produced. On
        failure we still return a usable flat summary plus a synthesised
        two-level tree so the doc-level record always has something to show.
        """
        try:
            return self._stores.summarizer.summarize_document_hierarchy(chunk_summaries)
        except Exception as e:
            logger.error("Document summarisation failed: %s", e)
            # Cheap fallback so the doc-level record still has text to display.
            flat = " ".join(s for s in chunk_summaries if s)[:2000]
            return flat, self._synthesize_tree(flat, chunk_summaries)

    @staticmethod
    def _synthesize_tree(root_summary: str, chunk_summaries: list[str]) -> SummaryNode | None:
        """Build a minimal two-level tree (root + one leaf per chunk summary).

        Used only on the fallback path where the hierarchical summariser did
        not return a tree, so the UI still renders structure.
        """
        leaves: list[SummaryNode] = [{"summary": s, "children": []} for s in chunk_summaries if s]
        if not leaves:
            return None
        return {"summary": root_summary, "children": leaves}

    def _choose_document_vector(self, summary: str, chunk_embeddings: list[list[float]]) -> list[float]:
        """Embed the doc summary, or fall back to centroid of chunk embeddings.

        Previously this defaulted to `chunk_embeddings[0]`, biasing
        document-level retrieval toward whatever happened to appear first
        in the source. The centroid is a much better neutral baseline.
        """
        if summary:
            try:
                return self._stores.embedder.embed(summary)
            except Exception as e:
                logger.error("Doc summary embedding failed; using centroid: %s", e)
        else:
            logger.warning("Doc summary empty; using centroid of chunk embeddings")
        return _centroid(chunk_embeddings)

    def _extract_and_store_triplets(
        self,
        *,
        tenant_id: str,
        document_key: str,
        chunks: list[str],
        tags: list[str],
        access_keys: list[int],
    ) -> int:
        """Extract triplets from all chunks in parallel, then batch-insert."""

        def extract_safe(chunk: str) -> list[tuple[str, str, str]]:
            try:
                return self._stores.extractor.extract(chunk)
            except Exception as e:
                logger.warning("Triplet extraction failed for one chunk: %s", e)
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=_TRIPLET_WORKERS) as exe:
            per_chunk = list(exe.map(extract_safe, chunks))

        all_triplets: list[tuple[str, str, str]] = [t for sub in per_chunk for t in sub]
        if not all_triplets:
            return 0

        try:
            self._stores.graph.insert_triplets(
                tenant_id=tenant_id,
                triplets=all_triplets,
                document_key=document_key,
                tags=tags,
                access_keys=access_keys,
            )
        except Exception as e:
            logger.error("Batch triplet insert failed: %s", e)
            return 0
        return len(all_triplets)

    def _extract_and_store_facts(
        self,
        *,
        tenant_id: str,
        document_key: str,
        chunks: list[str],
        tags: list[str],
        access_keys: list[int],
    ) -> dict[str, int]:
        """Run the reified-claim fact pipeline over a document's chunks."""
        self._stores.ensure_fact_schema()
        fact_chunks = [Chunk(chunk_id=f"{document_key}#{idx}", text=text) for idx, text in enumerate(chunks)]
        try:
            return self._stores.fact_pipeline.ingest_document(
                tenant_id,
                document_key,
                fact_chunks,
                tags=tuple(tags),
                access_keys=tuple(access_keys),
            )
        except Exception as e:
            logger.error("Fact ingest failed for %s: %s", document_key, e)
            return {"claims_extracted": 0}

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

        if self._stores.settings.use_fact_engine:
            try:
                self._stores.fact_store.delete_by_document_key(tenant_id, document_key)
            except Exception as e:
                logger.error("Fact-store delete failed for %s: %s", document_key, e)

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

    def remove_tenant(self, tenant_id: str) -> None:
        """Delete every vector collection + graph node for a tenant.

        Called when an org is deleted upstream. Both stores handle missing
        state idempotently, and a failure on one store doesn't block the
        other — cascading deletes should be best-effort so the upstream
        org deletion doesn't get blocked by a transient infra outage.
        """
        try:
            self._stores.vector.delete_tenant(tenant_id)
        except Exception as e:
            logger.error("Vector tenant delete failed for %s: %s", tenant_id, e)
        try:
            self._stores.graph.delete_tenant(tenant_id)
        except Exception as e:
            logger.error("Graph tenant delete failed for %s: %s", tenant_id, e)
        if self._stores.settings.use_fact_engine:
            try:
                self._stores.fact_store.delete_tenant(tenant_id)
            except Exception as e:
                logger.error("Fact-store tenant delete failed for %s: %s", tenant_id, e)
