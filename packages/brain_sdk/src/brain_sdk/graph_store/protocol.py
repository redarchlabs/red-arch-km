"""Protocol for graph store implementations."""

from __future__ import annotations

from typing import Any, Protocol


class GraphStore(Protocol):
    """Interface for knowledge graph operations."""

    def initialize_tenant(self, tenant_id: str) -> None:
        """Initialize graph schema/constraints for a tenant."""
        ...

    def insert_triplet(
        self,
        tenant_id: str,
        subj: str,
        pred: str,
        obj: str,
        *,
        document_key: str | None = None,
        subj_tags: list[str] | None = None,
        obj_tags: list[str] | None = None,
        subj_access: list[int] | None = None,
        obj_access: list[int] | None = None,
    ) -> None:
        """Insert a single subject-predicate-object triplet."""
        ...

    def insert_triplets(
        self,
        tenant_id: str,
        triplets: list[tuple[str, str, str]],
        *,
        document_key: str | None = None,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
    ) -> None:
        """Insert multiple triplets."""
        ...

    def fuzzy_relationship_search(
        self,
        tenant_id: str,
        term: str,
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search triplets by fuzzy term matching."""
        ...

    def fuzzy_entity_search(
        self,
        tenant_id: str,
        term: str,
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search entities by fuzzy name matching."""
        ...

    def delete_by_document_key(self, tenant_id: str, document_key: str) -> None:
        """Delete all nodes and relationships for a document."""
        ...

    def update_metadata(
        self,
        tenant_id: str,
        document_key: str,
        *,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
    ) -> None:
        """Update metadata on all nodes for a document."""
        ...

    def close(self) -> None:
        """Close the graph store connection."""
        ...
