"""Protocol for vector store implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """A single vector point to upsert."""

    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search hit."""

    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    """Interface for vector database operations."""

    def ensure_collections(self, tenant_id: str, *, reset: bool = False) -> None:
        """Ensure chunk and document collections exist for a tenant."""
        ...

    def upsert_vectors(
        self,
        tenant_id: str,
        records: list[VectorRecord],
        *,
        collection_type: str = "chunks",
    ) -> None:
        """Upsert vector records into a collection."""
        ...

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
        """Semantic search over chunk vectors."""
        ...

    def delete_document(self, tenant_id: str, document_key: str) -> None:
        """Delete all vectors associated with a document key."""
        ...

    def document_exists(self, tenant_id: str, document_key: str) -> bool:
        """Check if a document exists in the vector store."""
        ...

    def update_metadata(
        self,
        tenant_id: str,
        document_key: str,
        *,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
        title: str | None = None,
    ) -> None:
        """Update tags, access keys, and title for all vectors of a document."""
        ...

    def get_document_chunks(
        self,
        tenant_id: str,
        document_key: str,
        *,
        limit: int = 500,
    ) -> list[SearchResult]:
        """Return all chunks for a document ordered by chunk_order."""
        ...

    def get_document_record(self, tenant_id: str, document_key: str) -> SearchResult | None:
        """Return the doc-level record (summary + summary_tree) or None if absent."""
        ...

    def delete_tenant(self, tenant_id: str) -> None:
        """Delete both chunk and document collections for a tenant.

        Idempotent — missing collections are silently ignored so callers
        can cascade org-deletion without checking state first.
        """
        ...
