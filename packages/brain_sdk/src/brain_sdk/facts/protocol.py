"""Protocol for fact-store implementations.

The fact store is the spine of the knowledge engine: canonical entities and
reified claims with provenance and bi-temporal state. It exposes the primitives
the ingest pipeline writes through and the agentic query tools read through.
"""

from __future__ import annotations

from typing import Any, Protocol

from brain_sdk.facts.models import Claim, Entity


class FactStore(Protocol):
    """Interface for the reified-claim knowledge store."""

    def ensure_schema(self, *, embedding_dim: int | None = None) -> None:
        """Create global constraints and indexes (idempotent).

        ``embedding_dim`` enables the entity vector index when provided.
        """
        ...

    def initialize_tenant(self, tenant_id: str) -> None:
        """Prepare per-tenant state (idempotent)."""
        ...

    def upsert_entities(self, tenant_id: str, entities: list[Entity]) -> None:
        """Create/update canonical entities (merged by ``entity_id``)."""
        ...

    def get_entity(self, tenant_id: str, entity_id: str) -> Entity | None:
        """Fetch a canonical entity by id."""
        ...

    def add_aliases(self, tenant_id: str, entity_id: str, aliases: list[str]) -> None:
        """Record surface forms that resolved to this canonical entity."""
        ...

    def find_entities(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        embedding: list[float] | None = None,
        limit: int = 10,
    ) -> list[tuple[Entity, float]]:
        """Resolve/lookup entities by lexical name and/or vector similarity.

        Returns ``(entity, score)`` pairs, best first. Used both for
        ingest-time resolution and query-time entity lookup.
        """
        ...

    def insert_claims(self, tenant_id: str, claims: list[Claim]) -> dict[str, int]:
        """Insert claims, applying reconciliation.

        Returns a count of actions taken keyed by action name
        (``created``/``corroborated``/``superseded``/``contradicted``).
        """
        ...

    def query_claims(
        self,
        tenant_id: str,
        *,
        subject_id: str | None = None,
        predicate: str | None = None,
        object_id: str | None = None,
        object_value: str | None = None,
        as_of: str | None = None,
        statuses: list[str] | None = None,
        access_keys: list[int] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Structured claim query — exact and aggregative retrieval.

        ``as_of`` restricts to claims whose validity window contains that
        timestamp (temporal "truth as-of"). By default only ``active`` claims
        are returned.
        """
        ...

    def neighborhood(
        self,
        tenant_id: str,
        entity_id: str,
        *,
        hops: int = 1,
        access_keys: list[int] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Bounded relationship expansion around an entity (multi-hop)."""
        ...

    def iter_entities(self, tenant_id: str) -> list[Entity]:
        """All canonical entities for a tenant."""
        ...

    def entity_relationships(self, tenant_id: str) -> list[tuple[str, str]]:
        """Active entity→entity edges (subject_id, object_id) for community detection."""
        ...

    def upsert_community(self, tenant_id: str, community_id: str, summary: str, member_ids: list[str]) -> None:
        """Store/refresh a derived community summary."""
        ...

    def get_communities(self, tenant_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve community summaries, largest first."""
        ...

    def delete_by_document_key(self, tenant_id: str, document_key: str) -> None:
        """Remove provenance from a document; drop claims/entities left unsupported."""
        ...

    def delete_tenant(self, tenant_id: str) -> None:
        """Delete all graph data for a tenant (idempotent)."""
        ...

    def close(self) -> None:
        """Close the underlying connection."""
        ...
