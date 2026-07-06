"""Integration tests for the Neo4j fact store.

Exercises the reconciliation truth mechanics against a real Neo4j. Skips
cleanly when Neo4j is unreachable, so the suite stays green in environments
without the infra stack. Each test runs in its own throwaway tenant and tears
it down afterward.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from brain_sdk.facts.models import Claim, ClaimStatus, Entity, ObjectType, Provenance
from brain_sdk.facts.neo4j_fact_store import Neo4jFactStore

_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_USER = os.environ.get("NEO4J_USER", "neo4j")
_PWD = os.environ.get("NEO4J_PASSWORD", "neo4j123!")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> Iterator[Neo4jFactStore]:
    try:
        s = Neo4jFactStore(_URI, _USER, _PWD)
        s._run("RETURN 1 AS ok")  # noqa: SLF001 — probe connectivity
    except Exception as exc:  # noqa: BLE001 — any connection failure → skip
        pytest.skip(f"Neo4j unavailable: {exc}")
    s.ensure_schema()
    yield s
    s.close()


@pytest.fixture
def tenant(store: Neo4jFactStore) -> Iterator[str]:
    tid = "t_" + uuid.uuid4().hex[:12]
    yield tid
    store.delete_tenant(tid)


def _entity(tid: str, name: str, typ: str = "ORG") -> Entity:
    return Entity.make(tenant_id=tid, canonical_name=name, type=typ)


def _prov(document_key: str, chunk_id: str) -> Provenance:
    return Provenance(
        document_key=document_key,
        chunk_id=chunk_id,
        text_span="…supporting sentence…",
        extractor_model="test",
    )


def _literal_claim(
    tid: str,
    subj: Entity,
    predicate: str,
    obj: str,
    *,
    recorded_at: str = "2024-01-01T00:00:00+00:00",
    valid_from: str | None = None,
    document_key: str = "docA",
    chunk_id: str = "docA#0",
) -> Claim:
    return Claim(
        tenant_id=tid,
        subject_id=subj.entity_id,
        predicate=predicate,
        object_type=ObjectType.TEXT,
        object_value=obj,
        recorded_at=recorded_at,
        valid_from=valid_from,
        provenance=(_prov(document_key, chunk_id),),
    )


class TestCreateAndQuery:
    def test_create_then_query(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme Corp")
        store.upsert_entities(tenant, [acme])
        counts = store.insert_claims(tenant, [_literal_claim(tenant, acme, "headquartered_in", "Paris")])
        assert counts["created"] == 1

        rows = store.query_claims(tenant, subject_id=acme.entity_id)
        assert len(rows) == 1
        assert rows[0]["subject"] == "Acme Corp"
        assert rows[0]["predicate"] == "headquartered_in"
        assert rows[0]["object"] == "Paris"

    def test_entity_lookup_by_name(self, store: Neo4jFactStore, tenant: str) -> None:
        store.upsert_entities(tenant, [_entity(tenant, "Globex International")])
        hits = store.find_entities(tenant, name="Globex")
        assert any(e.canonical_name == "Globex International" for e, _ in hits)


class TestReconciliation:
    def test_repeat_corroborates(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        store.upsert_entities(tenant, [acme])
        store.insert_claims(tenant, [_literal_claim(tenant, acme, "headquartered_in", "Paris", chunk_id="docA#0")])
        counts = store.insert_claims(
            tenant,
            [_literal_claim(tenant, acme, "headquartered_in", "Paris", document_key="docB", chunk_id="docB#1")],
        )
        assert counts["corroborated"] == 1
        assert counts["created"] == 0

    def test_functional_newer_supersedes(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        store.upsert_entities(tenant, [acme])
        store.insert_claims(
            tenant,
            [_literal_claim(tenant, acme, "headquartered_in", "London", recorded_at="2020-01-01T00:00:00+00:00")],
        )
        counts = store.insert_claims(
            tenant,
            [_literal_claim(tenant, acme, "headquartered_in", "Paris", recorded_at="2024-01-01T00:00:00+00:00")],
        )
        assert counts["superseded"] == 1

        active = store.query_claims(tenant, subject_id=acme.entity_id)
        assert [r["object"] for r in active] == ["Paris"]

        history = store.query_claims(
            tenant, subject_id=acme.entity_id, statuses=[ClaimStatus.SUPERSEDED.value]
        )
        assert [r["object"] for r in history] == ["London"]

    def test_unorderable_contradicts(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        store.upsert_entities(tenant, [acme])
        same_time = "2024-01-01T00:00:00+00:00"
        store.insert_claims(tenant, [_literal_claim(tenant, acme, "headquartered_in", "London", recorded_at=same_time)])
        counts = store.insert_claims(
            tenant, [_literal_claim(tenant, acme, "headquartered_in", "Paris", recorded_at=same_time)]
        )
        assert counts["contradicted"] == 1

        # Neither is 'active' any more — the conflict is surfaced, not resolved.
        assert store.query_claims(tenant, subject_id=acme.entity_id) == []
        flagged = store.query_claims(
            tenant, subject_id=acme.entity_id, statuses=[ClaimStatus.CONTRADICTED.value]
        )
        assert {r["object"] for r in flagged} == {"London", "Paris"}


class TestTemporalAndProvenance:
    def test_as_of_filters_by_validity(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        store.upsert_entities(tenant, [acme])
        store.insert_claims(
            tenant,
            [
                _literal_claim(
                    tenant, acme, "headquartered_in", "London",
                    recorded_at="2010-01-01T00:00:00+00:00", valid_from="2010-01-01T00:00:00+00:00",
                )
            ],
        )
        store.insert_claims(
            tenant,
            [
                _literal_claim(
                    tenant, acme, "headquartered_in", "Paris",
                    recorded_at="2020-01-01T00:00:00+00:00", valid_from="2020-01-01T00:00:00+00:00",
                )
            ],
        )
        # As of 2015, the London value was the truth of record.
        as_of_2015 = store.query_claims(
            tenant, subject_id=acme.entity_id, as_of="2015-01-01T00:00:00+00:00",
            statuses=[ClaimStatus.ACTIVE.value, ClaimStatus.SUPERSEDED.value],
        )
        assert [r["object"] for r in as_of_2015] == ["London"]

    def test_purge_document_keeps_corroborated_claim(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        store.upsert_entities(tenant, [acme])
        # Same fact sourced from two documents.
        store.insert_claims(tenant, [_literal_claim(tenant, acme, "headquartered_in", "Paris", chunk_id="docA#0")])
        store.insert_claims(
            tenant,
            [_literal_claim(tenant, acme, "headquartered_in", "Paris", document_key="docB", chunk_id="docB#0")],
        )
        store.delete_by_document_key(tenant, "docA")
        # docB still sources it → the claim survives.
        assert [r["object"] for r in store.query_claims(tenant, subject_id=acme.entity_id)] == ["Paris"]

        store.delete_by_document_key(tenant, "docB")
        # Now unsupported → gone.
        assert store.query_claims(tenant, subject_id=acme.entity_id) == []


class TestIsolationAndTraversal:
    def test_tenant_isolation(self, store: Neo4jFactStore) -> None:
        a = "t_" + uuid.uuid4().hex[:12]
        b = "t_" + uuid.uuid4().hex[:12]
        try:
            acme_a = _entity(a, "Acme")
            store.upsert_entities(a, [acme_a])
            store.insert_claims(a, [_literal_claim(a, acme_a, "headquartered_in", "Paris")])
            # Tenant B, querying by A's entity id, sees nothing.
            assert store.query_claims(b, subject_id=acme_a.entity_id) == []
            assert store.find_entities(b, name="Acme") == []
        finally:
            store.delete_tenant(a)
            store.delete_tenant(b)

    def test_digest_community_roundtrip(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        widgets = _entity(tenant, "Widgets Inc")
        store.upsert_entities(tenant, [acme, widgets])
        store.insert_claims(
            tenant,
            [
                Claim(
                    tenant_id=tenant, subject_id=acme.entity_id, predicate="acquired",
                    object_type=ObjectType.ENTITY, object_id=widgets.entity_id,
                    provenance=(_prov("docA", "docA#0"),),
                )
            ],
        )
        # iter_entities + entity_relationships feed community detection.
        ids = {e.entity_id for e in store.iter_entities(tenant)}
        assert {acme.entity_id, widgets.entity_id} <= ids
        assert (acme.entity_id, widgets.entity_id) in store.entity_relationships(tenant)

        store.upsert_community(tenant, f"{tenant}:c1", "Acme and Widgets.", [acme.entity_id, widgets.entity_id])
        communities = store.get_communities(tenant)
        assert communities[0]["summary"] == "Acme and Widgets."
        assert communities[0]["size"] == 2

    def test_neighborhood_returns_entity_relationships(self, store: Neo4jFactStore, tenant: str) -> None:
        acme = _entity(tenant, "Acme")
        widgets = _entity(tenant, "Widgets Inc")
        store.upsert_entities(tenant, [acme, widgets])
        store.insert_claims(
            tenant,
            [
                Claim(
                    tenant_id=tenant,
                    subject_id=acme.entity_id,
                    predicate="acquired",
                    object_type=ObjectType.ENTITY,
                    object_id=widgets.entity_id,
                    provenance=(_prov("docA", "docA#0"),),
                )
            ],
        )
        nbrs = store.neighborhood(tenant, acme.entity_id)
        assert any(r["object"] == "Widgets Inc" and r["predicate"] == "acquired" for r in nbrs)
