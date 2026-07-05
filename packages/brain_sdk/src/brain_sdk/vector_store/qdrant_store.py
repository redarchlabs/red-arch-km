"""Qdrant vector store implementation."""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from brain_sdk.vector_store.protocol import SearchResult, VectorRecord

logger = logging.getLogger(__name__)

_RANGE_INDEX_ERROR = "No range index for `order_by` key"


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class QdrantVectorStore:
    """VectorStore implementation backed by Qdrant."""

    def __init__(
        self,
        url: str,
        api_key: str = "",
        *,
        chunk_suffix: str = "chunks",
        document_suffix: str = "documents",
        dimension: int = 1536,
    ) -> None:
        self._client = QdrantClient(
            url=url,
            api_key=api_key or None,
            prefer_grpc=False,
        )
        self._chunk_suffix = chunk_suffix
        self._doc_suffix = document_suffix
        self._dimension = dimension

    def _chunk_collection(self, tenant_id: str) -> str:
        return f"{tenant_id}-{self._chunk_suffix}"

    def _doc_collection(self, tenant_id: str) -> str:
        return f"{tenant_id}-{self._doc_suffix}"

    def ensure_collections(self, tenant_id: str, *, reset: bool = False) -> None:
        for name in [self._chunk_collection(tenant_id), self._doc_collection(tenant_id)]:
            if reset:
                self._recreate_collection(name)
            else:
                self._ensure_collection(name)

    def _recreate_collection(self, name: str) -> None:
        with contextlib.suppress(Exception):
            self._client.delete_collection(collection_name=name)
        self._create_collection(name)

    def _ensure_collection(self, name: str) -> None:
        try:
            self._client.get_collection(name)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "doesn't exist" in msg or "not found" in msg:
                self._create_collection(name)
            else:
                raise

    def _create_collection(self, name: str) -> None:
        self._client.create_collection(
            collection_name=name,
            vectors_config={
                "embedding": rest.VectorParams(
                    size=self._dimension,
                    distance=rest.Distance.COSINE,
                )
            },
        )
        logger.info("Created Qdrant collection: %s", name)

    def upsert_vectors(
        self,
        tenant_id: str,
        records: list[VectorRecord],
        *,
        collection_type: str = "chunks",
    ) -> None:
        collection = (
            self._chunk_collection(tenant_id) if collection_type == "chunks" else self._doc_collection(tenant_id)
        )
        points = [
            rest.PointStruct(
                id=r.id or str(uuid.uuid4()),
                vector={"embedding": r.vector},
                payload=r.payload,
            )
            for r in records
        ]

        for batch in _batched(points, 500):
            self._client.upsert(collection_name=collection, points=batch, wait=True)
            logger.debug("Upserted %d points to %s", len(batch), collection)

    def search(
        self,
        tenant_id: str,
        query_vector: list[float],
        *,
        limit: int = 5,
        access_keys: list[int] | None = None,
        required_tags: list[str] | None = None,
        any_tags: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[SearchResult]:
        collection = self._chunk_collection(tenant_id)
        must: list[rest.Condition] = []

        if filters:
            for f in filters:
                term = f.get("term", {})
                for field_name, value in term.items():
                    must.append(rest.FieldCondition(key=field_name, match=rest.MatchValue(value=value)))

        if required_tags:
            # AND semantics: every required tag must be present.
            must.extend(rest.FieldCondition(key="tags", match=rest.MatchValue(value=tag)) for tag in required_tags)

        if any_tags:
            # OR semantics within the set: the doc must carry at least one of
            # these tags. Used for folder scoping — a doc lives in exactly one
            # folder, so "in folder A or folder B" is `tags MatchAny [folder:A,
            # folder:B]`. Still ANDs with required_tags / access_keys.
            must.append(rest.FieldCondition(key="tags", match=rest.MatchAny(any=any_tags)))

        if access_keys:
            must.append(rest.FieldCondition(key="access_keys", match=rest.MatchAny(any=access_keys)))

        query_filter = rest.Filter(must=must) if must else None

        results = self._client.query_points(
            collection_name=collection,
            query=query_vector,
            using="embedding",
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        ).points

        return [
            SearchResult(
                id=str(p.id),
                score=p.score,
                payload={
                    "text": payload.get("text", ""),
                    "summary": payload.get("summary", ""),
                    "chunk_order": payload.get("chunk_order", 0),
                    "document_id": payload.get("document_id", ""),
                    "document_key": payload.get("document_key", ""),
                    "document_title": payload.get("document_title", ""),
                    "type": payload.get("type", ""),
                },
            )
            for p in results
            if p
            for payload in (p.payload or {},)
        ]

    def delete_document(self, tenant_id: str, document_key: str) -> None:
        conditions: list[rest.Condition] = [
            rest.FieldCondition(key="document_key", match=rest.MatchValue(value=document_key)),
            rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
        ]
        q_filter = rest.Filter(must=conditions)

        for collection in [self._chunk_collection(tenant_id), self._doc_collection(tenant_id)]:
            self._client.delete(collection_name=collection, points_selector=q_filter)
        logger.info("Deleted document %s from tenant %s", document_key, tenant_id)

    def delete_tenant(self, tenant_id: str) -> None:
        """Drop both collections for a tenant. Idempotent on missing collections."""
        for collection in (
            self._chunk_collection(tenant_id),
            self._doc_collection(tenant_id),
        ):
            try:
                self._client.delete_collection(collection_name=collection)
            except Exception as e:  # noqa: BLE001 — Qdrant raises generic on 404
                msg = str(e).lower()
                if "not found" in msg or "doesn't exist" in msg or "404" in msg:
                    continue
                raise
        logger.info("Deleted Qdrant collections for tenant %s", tenant_id)

    def document_exists(self, tenant_id: str, document_key: str) -> bool:
        conditions: list[rest.Condition] = [
            rest.FieldCondition(key="document_key", match=rest.MatchValue(value=document_key)),
            rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
        ]
        q_filter = rest.Filter(must=conditions)
        try:
            count = self._client.count(
                collection_name=self._doc_collection(tenant_id),
                count_filter=q_filter,
            ).count
            return count > 0
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "doesn't exist" in msg or "not found" in msg:
                return False
            raise

    def update_metadata(
        self,
        tenant_id: str,
        document_key: str,
        *,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
        title: str | None = None,
    ) -> None:
        collection = self._chunk_collection(tenant_id)
        conditions: list[rest.Condition] = [
            rest.FieldCondition(key="document_key", match=rest.MatchValue(value=document_key)),
            rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
        ]
        query_filter = rest.Filter(must=conditions)

        payload: dict[str, Any] = {}
        if tags is not None:
            payload["tags"] = tags
        if access_keys is not None:
            payload["access_keys"] = access_keys
        if title is not None:
            payload["document_title"] = title

        if not payload:
            return

        next_offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=500,
                offset=next_offset,
            )
            if not points:
                break

            point_ids = [p.id for p in points]
            self._client.set_payload(
                collection_name=collection,
                payload=payload,
                points=point_ids,
            )

            if next_offset is None:
                break

    def get_document_chunks(
        self,
        tenant_id: str,
        document_key: str,
        *,
        limit: int = 500,
    ) -> list[SearchResult]:
        """Return all chunks for a document, ordered by chunk_order."""
        collection = self._chunk_collection(tenant_id)
        conditions: list[rest.Condition] = [
            rest.FieldCondition(key="document_key", match=rest.MatchValue(value=document_key)),
            rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
        ]
        query_filter = rest.Filter(must=conditions)

        all_results: list[SearchResult] = []
        next_offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=min(500, limit - len(all_results)),
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            all_results.extend(SearchResult(id=str(p.id), score=0.0, payload=p.payload or {}) for p in points)
            if next_offset is None or len(all_results) >= limit:
                break

        all_results.sort(key=lambda r: int(r.payload.get("chunk_order", 0)))
        return all_results

    def get_document_record(self, tenant_id: str, document_key: str) -> SearchResult | None:
        """Return the doc-level record (summary + summary_tree) or None.

        Reads from the documents collection where the single per-document
        vector lives. A missing collection is treated as "not found" so the
        caller can 404 cleanly while ingestion is still in flight.
        """
        collection = self._doc_collection(tenant_id)
        conditions: list[rest.Condition] = [
            rest.FieldCondition(key="document_key", match=rest.MatchValue(value=document_key)),
            rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
        ]
        query_filter = rest.Filter(must=conditions)

        try:
            points, _ = self._client.scroll(
                collection_name=collection,
                scroll_filter=query_filter,
                limit=1,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as e:  # noqa: BLE001 — Qdrant raises generic on missing collection
            msg = str(e).lower()
            if "doesn't exist" in msg or "not found" in msg:
                return None
            raise

        if not points:
            return None
        p = points[0]
        return SearchResult(id=str(p.id), score=0.0, payload=p.payload or {})

    def _wait_collection_ready(self, collection: str, *, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        while True:
            info = self._client.get_collection(collection_name=collection)
            if info.status == "green" and getattr(info, "pending_operations", 0) == 0:
                return
            if time.time() > deadline:
                msg = f"Collection {collection} not ready after {timeout}s"
                raise TimeoutError(msg)
            time.sleep(1)
