"""Neo4j schema for the fact store: constraints and indexes.

Constraints are declared on the base labels (``:Entity``, ``:Claim``,
``:Chunk``). Every node also carries a dynamic ``:Tenant_<org>`` label for
isolation; because ``entity_id``/``claim_id`` embed the tenant, a single global
uniqueness constraint is safe across tenants. The full-text and vector indexes
on ``:Entity`` span all tenants; queries filter by the tenant label.
"""

from __future__ import annotations

# Constraints + plain indexes (no parameters).
SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id_unique IF NOT EXISTS FOR (c:Claim) REQUIRE c.claim_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (k:Chunk) REQUIRE k.chunk_id IS UNIQUE",
    "CREATE INDEX claim_predicate IF NOT EXISTS FOR (c:Claim) ON (c.predicate)",
    "CREATE INDEX claim_status IF NOT EXISTS FOR (c:Claim) ON (c.status)",
    "CREATE INDEX claim_dedup IF NOT EXISTS FOR (c:Claim) ON (c.dedup_key)",
    "CREATE INDEX claim_subject IF NOT EXISTS FOR (c:Claim) ON (c.subject_id)",
    "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.canonical_name)",
    "CREATE INDEX chunk_document IF NOT EXISTS FOR (k:Chunk) ON (k.document_key)",
    "CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS FOR (e:Entity) ON EACH [e.canonical_name, e.alias_text]",
)

ENTITY_VECTOR_INDEX = "entity_embedding"
ENTITY_FULLTEXT_INDEX = "entity_fulltext"


def vector_index_statement(dimension: int) -> str:
    """Vector index over entity embeddings, for ANN resolution + semantic entry."""
    return (
        f"CREATE VECTOR INDEX {ENTITY_VECTOR_INDEX} IF NOT EXISTS "
        "FOR (e:Entity) ON (e.embedding) "
        "OPTIONS {indexConfig: {"
        f"`vector.dimensions`: {int(dimension)}, "
        "`vector.similarity_function`: 'cosine'}}"
    )
