"""Neo4j-backed fact store: canonical entities + reified claims.

Graph shape::

    (s:Entity)-[:SUBJECT]->(c:Claim)-[:OBJECT]->(o:Entity)   # entity object
    (c:Claim {object_value})                                  # literal object
    (c:Claim)-[:SOURCED_FROM]->(:Chunk)-[:PART_OF]->(:Document)
    (c:Claim)-[:SUPERSEDES|CONTRADICTS]->(c2:Claim)

Every node also carries a ``:Tenant_<org>`` label; every read/write injects that
label server-side (isolation is never delegated to a caller-supplied filter).
Reconciliation (create / corroborate / supersede / contradict) is delegated to
the pure :func:`brain_sdk.facts.reconciliation.reconcile`; this module only
reads the relevant existing state and executes the decision.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction

from brain_sdk.facts.models import Claim, ClaimStatus, Entity, ObjectType, now_iso
from brain_sdk.facts.predicates import PredicateRegistry
from brain_sdk.facts.reconciliation import ReconcileAction, reconcile
from brain_sdk.facts.schema import SCHEMA_STATEMENTS, vector_index_statement

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")


class Neo4jFactStore:
    """FactStore implementation backed by Neo4j."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        database: str | None = None,
        registry: PredicateRegistry | None = None,
    ) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = database
        self._registry = registry or PredicateRegistry()

    # ---- infrastructure -------------------------------------------------

    def _tenant_label(self, tenant_id: str) -> str:
        return f"Tenant_{_LABEL_RE.sub('_', tenant_id)}"

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self._db) as sess:
            return [r.data() for r in sess.run(query, **params)]

    def ensure_schema(self, *, embedding_dim: int | None = None) -> None:
        for stmt in SCHEMA_STATEMENTS:
            self._run(stmt)
        if embedding_dim:
            self._run(vector_index_statement(embedding_dim))
        logger.info("Fact-store schema ensured (embedding_dim=%s)", embedding_dim)

    def initialize_tenant(self, tenant_id: str) -> None:
        # Tenant isolation is label-based and schema is global, so there is no
        # per-tenant DDL. Kept for protocol symmetry / future per-tenant setup.
        logger.info("Initialized fact-store tenant: %s", tenant_id)

    # ---- entities -------------------------------------------------------

    def upsert_entities(self, tenant_id: str, entities: list[Entity]) -> None:
        if not entities:
            return
        label = self._tenant_label(tenant_id)
        rows = [
            {
                "entity_id": e.entity_id,
                "canonical_name": e.canonical_name,
                "type": e.type,
                "aliases": list(e.aliases),
                "alias_text": " ".join((e.canonical_name, *e.aliases)),
                "embedding": list(e.embedding) if e.embedding is not None else None,
            }
            for e in entities
        ]
        self._run(
            f"""
            UNWIND $rows AS row
            MERGE (e:Entity {{entity_id: row.entity_id}})
            SET e:{label},
                e.tenant_id = $tenant_id,
                e.canonical_name = row.canonical_name,
                e.type = row.type,
                e.aliases = row.aliases,
                e.alias_text = row.alias_text
            FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END |
                SET e.embedding = row.embedding)
            """,
            rows=rows,
            tenant_id=tenant_id,
        )

    def add_aliases(self, tenant_id: str, entity_id: str, aliases: list[str]) -> None:
        """Record surface forms that resolved to this canonical entity."""
        if not aliases:
            return
        label = self._tenant_label(tenant_id)
        self._run(
            f"""
            MATCH (e:Entity:{label} {{entity_id: $id}})
            WITH e, apoc.coll.toSet(coalesce(e.aliases, []) + $aliases) AS merged
            SET e.aliases = merged,
                e.alias_text = trim(e.canonical_name + ' ' + apoc.text.join(merged, ' '))
            """,
            id=entity_id,
            aliases=aliases,
        )

    def get_entity(self, tenant_id: str, entity_id: str) -> Entity | None:
        label = self._tenant_label(tenant_id)
        rows = self._run(
            f"MATCH (e:Entity:{label} {{entity_id: $id}}) "
            "RETURN e.entity_id AS id, e.canonical_name AS name, e.type AS type, e.aliases AS aliases",
            id=entity_id,
        )
        if not rows:
            return None
        r = rows[0]
        return Entity(
            entity_id=r["id"],
            tenant_id=tenant_id,
            canonical_name=r["name"],
            type=r["type"],
            aliases=tuple(r.get("aliases") or ()),
        )

    def find_entities(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        embedding: list[float] | None = None,
        limit: int = 10,
    ) -> list[tuple[Entity, float]]:
        label = self._tenant_label(tenant_id)
        found: dict[str, tuple[Entity, float]] = {}

        if embedding is not None:
            for r in self._run(
                f"""
                CALL db.index.vector.queryNodes('entity_embedding', $k, $embedding)
                YIELD node, score
                WHERE node:{label}
                RETURN node.entity_id AS id, node.canonical_name AS name,
                       node.type AS type, node.aliases AS aliases, score
                """,
                k=limit,
                embedding=embedding,
            ):
                found[r["id"]] = (self._row_entity(tenant_id, r), float(r["score"]))

        if name:
            # Lucene-escaped prefix query keeps the full-text lookup safe and fuzzy.
            escaped = re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", name)
            for r in self._run(
                f"""
                CALL db.index.fulltext.queryNodes('entity_fulltext', $q)
                YIELD node, score
                WHERE node:{label}
                RETURN node.entity_id AS id, node.canonical_name AS name,
                       node.type AS type, node.aliases AS aliases, score
                LIMIT $limit
                """,
                q=f"{escaped}~",
                limit=limit,
            ):
                # Keep the higher score if the entity surfaced via both indexes.
                prev = found.get(r["id"])
                score = float(r["score"])
                if prev is None or score > prev[1]:
                    found[r["id"]] = (self._row_entity(tenant_id, r), score)

        return sorted(found.values(), key=lambda pair: pair[1], reverse=True)[:limit]

    def _row_entity(self, tenant_id: str, r: dict[str, Any]) -> Entity:
        return Entity(
            entity_id=r["id"],
            tenant_id=tenant_id,
            canonical_name=r["name"],
            type=r["type"],
            aliases=tuple(r.get("aliases") or ()),
        )

    # ---- claims (write, with reconciliation) ----------------------------

    def insert_claims(self, tenant_id: str, claims: list[Claim]) -> dict[str, int]:
        counts = {"created": 0, "corroborated": 0, "superseded": 0, "contradicted": 0}
        if not claims:
            return counts
        label = self._tenant_label(tenant_id)
        with self._driver.session(database=self._db) as sess:
            for claim in claims:
                action = sess.execute_write(self._apply_claim, claim, label, self._registry)
                if action is ReconcileAction.CREATE:
                    counts["created"] += 1
                elif action is ReconcileAction.CORROBORATE:
                    counts["corroborated"] += 1
                elif action is ReconcileAction.SUPERSEDE:
                    counts["created"] += 1
                    counts["superseded"] += 1
                elif action is ReconcileAction.CONTRADICT:
                    counts["created"] += 1
                    counts["contradicted"] += 1
        logger.info("Inserted claims for tenant %s: %s", tenant_id, counts)
        return counts

    @staticmethod
    def _apply_claim(
        tx: ManagedTransaction,
        claim: Claim,
        label: str,
        registry: PredicateRegistry,
    ) -> ReconcileAction:
        cardinality = registry.cardinality(claim.predicate)

        same = tx.run(
            f"MATCH (c:Claim:{label} {{claim_id: $id}}) RETURN count(c) AS n",
            id=claim.claim_id,
        ).single()
        existing_same = claim if (same and same["n"]) else None

        conflicts: tuple[Claim, ...] = ()
        if existing_same is None and cardinality.value == "functional":
            rows = tx.run(
                f"""
                MATCH (s:Entity:{label} {{entity_id: $subject}})-[:SUBJECT]->(c:Claim:{label})
                WHERE c.predicate = $predicate AND c.status = 'active' AND c.object_key <> $object_key
                RETURN c.subject_id AS subject_id, c.predicate AS predicate,
                       c.object_type AS object_type, c.object_id AS object_id,
                       c.object_value AS object_value, c.valid_from AS valid_from,
                       c.recorded_at AS recorded_at
                """,
                subject=claim.subject_id,
                predicate=claim.predicate,
                object_key=claim.object_key,
            )
            conflicts = tuple(Neo4jFactStore._row_claim(claim.tenant_id, r.data()) for r in rows)

        result = reconcile(
            claim,
            cardinality=cardinality,
            existing_same=existing_same,
            active_conflicts=conflicts,
        )

        if result.action is ReconcileAction.CORROBORATE:
            Neo4jFactStore._corroborate(tx, claim, label)
            return result.action

        Neo4jFactStore._create_claim(tx, claim, label, result.new_status)

        if result.action is ReconcileAction.SUPERSEDE:
            valid_to = claim.valid_from or claim.recorded_at
            tx.run(
                f"""
                UNWIND $affected AS ak
                MATCH (new:Claim:{label} {{claim_id: $new_id}})
                MATCH (old:Claim:{label} {{claim_id: ak}})
                SET old.status = 'superseded',
                    old.valid_to = coalesce(old.valid_to, $valid_to)
                MERGE (new)-[:SUPERSEDES]->(old)
                """,
                affected=list(result.affected_keys),
                new_id=claim.claim_id,
                valid_to=valid_to,
            )
        elif result.action is ReconcileAction.CONTRADICT:
            tx.run(
                f"""
                UNWIND $affected AS ak
                MATCH (new:Claim:{label} {{claim_id: $new_id}})
                MATCH (other:Claim:{label} {{claim_id: ak}})
                SET other.status = 'contradicted'
                MERGE (new)-[:CONTRADICTS]->(other)
                MERGE (other)-[:CONTRADICTS]->(new)
                """,
                affected=list(result.affected_keys),
                new_id=claim.claim_id,
            )
        return result.action

    @staticmethod
    def _create_claim(tx: ManagedTransaction, claim: Claim, label: str, status: ClaimStatus) -> None:
        props = {
            "claim_id": claim.claim_id,
            "dedup_key": claim.dedup_key,
            "tenant_id": claim.tenant_id,
            "subject_id": claim.subject_id,
            "predicate": claim.predicate,
            "object_type": claim.object_type.value,
            "object_id": claim.object_id,
            "object_value": claim.object_value,
            "object_key": claim.object_key,
            "valid_from": claim.valid_from,
            "valid_to": claim.valid_to,
            "recorded_at": claim.recorded_at,
            "status": status.value,
            "confidence": claim.confidence,
            "access_keys": list(claim.access_keys),
            "tags": list(claim.tags),
        }
        tx.run(
            f"""
            MERGE (c:Claim {{claim_id: $props.claim_id}})
            ON CREATE SET c = $props, c:{label}, c.corroborations = 1
            ON MATCH SET c += $props
            WITH c
            MERGE (s:Entity {{entity_id: $subject_id}}) SET s:{label}
            MERGE (s)-[:SUBJECT]->(c)
            FOREACH (_ IN CASE WHEN $object_id IS NULL THEN [] ELSE [1] END |
                MERGE (o:Entity {{entity_id: $object_id}}) SET o:{label}
                MERGE (c)-[:OBJECT]->(o))
            """,
            props=props,
            subject_id=claim.subject_id,
            object_id=claim.object_id,
        )
        Neo4jFactStore._attach_provenance(tx, claim, label)

    @staticmethod
    def _corroborate(tx: ManagedTransaction, claim: Claim, label: str) -> None:
        tx.run(
            f"""
            MATCH (c:Claim:{label} {{claim_id: $id}})
            SET c.corroborations = coalesce(c.corroborations, 1) + 1,
                c.confidence = CASE WHEN $confidence > c.confidence THEN $confidence ELSE c.confidence END
            """,
            id=claim.claim_id,
            confidence=claim.confidence,
        )
        Neo4jFactStore._attach_provenance(tx, claim, label)

    @staticmethod
    def _attach_provenance(tx: ManagedTransaction, claim: Claim, label: str) -> None:
        if not claim.provenance:
            return
        prov = [
            {
                "chunk_id": p.chunk_id,
                "document_key": p.document_key,
                "text_span": p.text_span,
                "extractor_model": p.extractor_model,
                "extracted_at": p.extracted_at,
                "confidence": p.confidence,
            }
            for p in claim.provenance
        ]
        tx.run(
            f"""
            MATCH (c:Claim:{label} {{claim_id: $id}})
            UNWIND $prov AS p
            MERGE (k:Chunk {{chunk_id: p.chunk_id}})
                SET k:{label}, k.document_key = p.document_key
            MERGE (d:Document {{document_key: p.document_key}}) SET d:{label}
            MERGE (k)-[:PART_OF]->(d)
            MERGE (c)-[sf:SOURCED_FROM]->(k)
            SET sf.text_span = p.text_span,
                sf.extractor_model = p.extractor_model,
                sf.extracted_at = p.extracted_at,
                sf.confidence = p.confidence
            """,
            id=claim.claim_id,
            prov=prov,
        )

    @staticmethod
    def _row_claim(tenant_id: str, r: dict[str, Any]) -> Claim:
        object_type = ObjectType(r["object_type"])
        return Claim(
            tenant_id=tenant_id,
            subject_id=r["subject_id"],
            predicate=r["predicate"],
            object_type=object_type,
            object_id=r.get("object_id"),
            object_value=r.get("object_value"),
            valid_from=r.get("valid_from"),
            recorded_at=r.get("recorded_at") or now_iso(),
        )

    # ---- claims (read) --------------------------------------------------

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
        label = self._tenant_label(tenant_id)
        conds = ["c.status IN $statuses"]
        params: dict[str, Any] = {"statuses": statuses or [ClaimStatus.ACTIVE.value], "limit": limit}

        if subject_id:
            conds.append("c.subject_id = $subject_id")
            params["subject_id"] = subject_id
        if predicate:
            conds.append("c.predicate = $predicate")
            params["predicate"] = self._registry.normalize(predicate).key
        if object_id:
            conds.append("c.object_id = $object_id")
            params["object_id"] = object_id
        if object_value:
            conds.append("toLower(c.object_value) CONTAINS toLower($object_value)")
            params["object_value"] = object_value
        if as_of:
            conds.append("(c.valid_from IS NULL OR c.valid_from <= $as_of)")
            conds.append("(c.valid_to IS NULL OR c.valid_to > $as_of)")
            params["as_of"] = as_of
        if access_keys is not None:
            conds.append("(size(c.access_keys) = 0 OR any(k IN c.access_keys WHERE k IN $keys))")
            params["keys"] = access_keys

        return self._run(
            f"""
            MATCH (s:Entity:{label})-[:SUBJECT]->(c:Claim:{label})
            WHERE {' AND '.join(conds)}
            OPTIONAL MATCH (c)-[:OBJECT]->(o:Entity)
            RETURN s.canonical_name AS subject,
                   c.predicate AS predicate,
                   coalesce(o.canonical_name, c.object_value) AS object,
                   c.status AS status,
                   c.confidence AS confidence,
                   c.corroborations AS corroborations,
                   c.valid_from AS valid_from,
                   c.valid_to AS valid_to
            ORDER BY c.confidence DESC, c.corroborations DESC
            LIMIT $limit
            """,
            **params,
        )

    def neighborhood(
        self,
        tenant_id: str,
        entity_id: str,
        *,
        hops: int = 1,
        access_keys: list[int] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        # One hop = entity→claim→entity. The agent composes multi-hop by chaining
        # neighborhood() calls on returned neighbors, which is the agentic pattern;
        # a native quantified-path expansion is a query-engine follow-up.
        label = self._tenant_label(tenant_id)
        access_cond = ""
        params: dict[str, Any] = {"eid": entity_id, "limit": limit}
        if access_keys is not None:
            access_cond = "AND (size(c.access_keys) = 0 OR any(k IN c.access_keys WHERE k IN $keys))"
            params["keys"] = access_keys
        return self._run(
            f"""
            MATCH (start:Entity:{label} {{entity_id: $eid}})-[:SUBJECT|OBJECT]-(c:Claim:{label})
            WHERE c.status = 'active' {access_cond}
            MATCH (s:Entity)-[:SUBJECT]->(c)
            OPTIONAL MATCH (c)-[:OBJECT]->(o:Entity)
            RETURN s.canonical_name AS subject,
                   c.predicate AS predicate,
                   coalesce(o.canonical_name, c.object_value) AS object,
                   c.confidence AS confidence
            ORDER BY c.confidence DESC
            LIMIT $limit
            """,
            **params,
        )

    # ---- digest / community support ------------------------------------

    def iter_entities(self, tenant_id: str) -> list[Entity]:
        """All canonical entities for a tenant (for digest/community building)."""
        label = self._tenant_label(tenant_id)
        rows = self._run(
            f"MATCH (e:Entity:{label}) "
            "RETURN e.entity_id AS id, e.canonical_name AS name, e.type AS type, e.aliases AS aliases"
        )
        return [self._row_entity(tenant_id, r) for r in rows]

    def entity_relationships(self, tenant_id: str) -> list[tuple[str, str]]:
        """Active entity→entity edges (subject_id, object_id) for community detection."""
        label = self._tenant_label(tenant_id)
        rows = self._run(
            f"""
            MATCH (s:Entity:{label})-[:SUBJECT]->(c:Claim:{label})-[:OBJECT]->(o:Entity:{label})
            WHERE c.status = 'active'
            RETURN s.entity_id AS s, o.entity_id AS o
            """
        )
        return [(r["s"], r["o"]) for r in rows]

    def upsert_community(self, tenant_id: str, community_id: str, summary: str, member_ids: list[str]) -> None:
        """Store/refresh a community summary node (derived digest layer)."""
        label = self._tenant_label(tenant_id)
        self._run(
            f"""
            MERGE (k:Community {{community_id: $id}})
            SET k:{label}, k.tenant_id = $tenant_id, k.summary = $summary,
                k.members = $members, k.size = $size
            """,
            id=community_id,
            tenant_id=tenant_id,
            summary=summary,
            members=member_ids,
            size=len(member_ids),
        )

    def get_communities(self, tenant_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve community summaries, largest first (corpus orientation)."""
        label = self._tenant_label(tenant_id)
        return self._run(
            f"MATCH (k:Community:{label}) "
            "RETURN k.community_id AS community_id, k.summary AS summary, k.size AS size "
            "ORDER BY k.size DESC LIMIT $limit",
            limit=limit,
        )

    # ---- lifecycle ------------------------------------------------------

    def delete_by_document_key(self, tenant_id: str, document_key: str) -> None:
        label = self._tenant_label(tenant_id)
        # Drop this document's chunks (and their provenance edges); then remove
        # claims/entities left with no supporting evidence. Claims corroborated
        # by other documents survive because they still have SOURCED_FROM edges.
        self._run(f"MATCH (k:Chunk:{label} {{document_key: $dk}}) DETACH DELETE k", dk=document_key)
        self._run(f"MATCH (d:Document:{label} {{document_key: $dk}}) DETACH DELETE d", dk=document_key)
        self._run(f"MATCH (c:Claim:{label}) WHERE NOT (c)-[:SOURCED_FROM]->(:Chunk) DETACH DELETE c")
        self._run(f"MATCH (e:Entity:{label}) WHERE NOT (e)--(:Claim) DETACH DELETE e")
        logger.info("Purged document %s from fact store (tenant %s)", document_key, tenant_id)

    def delete_tenant(self, tenant_id: str) -> None:
        label = self._tenant_label(tenant_id)
        count = self._run(f"MATCH (n:{label}) RETURN count(n) AS c")
        n = count[0]["c"] if count else 0
        if n:
            self._run(f"MATCH (n:{label}) DETACH DELETE n")
        logger.info("Deleted %d fact-store nodes for tenant %s", n, tenant_id)

    def close(self) -> None:
        self._driver.close()
