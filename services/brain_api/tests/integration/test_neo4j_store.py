"""Integration tests for Neo4jGraphStore against a real Neo4j container."""

from __future__ import annotations

import uuid

import pytest
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore


pytestmark = pytest.mark.integration


class TestNeo4jStoreIntegration:
    def test_initialize_tenant_is_idempotent(
        self, graph_store: Neo4jGraphStore
    ) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        graph_store.initialize_tenant(tenant)
        graph_store.initialize_tenant(tenant)  # safe to call twice

    def test_insert_triplet_then_fuzzy_search(
        self, graph_store: Neo4jGraphStore
    ) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        graph_store.initialize_tenant(tenant)

        graph_store.insert_triplet(
            tenant,
            "Alice",
            "knows",
            "Bob",
            document_key="doc-1",
            subj_tags=["person"],
            obj_tags=["person"],
            subj_access=[100],
            obj_access=[100],
        )

        hits = graph_store.fuzzy_relationship_search(tenant, "alice")
        assert len(hits) > 0
        assert any(h.get("subj") == "Alice" for h in hits)

    def test_tenant_isolation_for_entities(
        self, graph_store: Neo4jGraphStore
    ) -> None:
        tenant_a = f"ta-{uuid.uuid4().hex[:8]}"
        tenant_b = f"tb-{uuid.uuid4().hex[:8]}"

        graph_store.initialize_tenant(tenant_a)
        graph_store.initialize_tenant(tenant_b)

        graph_store.insert_triplet(tenant_a, "Secret-A", "in", "Vault", document_key="a1")
        graph_store.insert_triplet(tenant_b, "Secret-B", "in", "Vault", document_key="b1")

        # Fuzzy entity search scoped to tenant A shouldn't surface tenant B
        hits_a = graph_store.fuzzy_entity_search(tenant_a, "secret")
        hits_b = graph_store.fuzzy_entity_search(tenant_b, "secret")

        names_a = {h["name"] for h in hits_a}
        names_b = {h["name"] for h in hits_b}
        assert "Secret-A" in names_a
        assert "Secret-B" not in names_a
        assert "Secret-B" in names_b
        assert "Secret-A" not in names_b

    def test_delete_by_document_key(self, graph_store: Neo4jGraphStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        graph_store.initialize_tenant(tenant)

        graph_store.insert_triplet(
            tenant, "DocA-Node1", "relates_to", "DocA-Node2", document_key="doc-a"
        )
        graph_store.insert_triplet(
            tenant, "DocB-Node1", "relates_to", "DocB-Node2", document_key="doc-b"
        )

        graph_store.delete_by_document_key(tenant, "doc-a")

        hits = graph_store.fuzzy_entity_search(tenant, "DocA")
        assert hits == []
        # Doc B entities survive
        hits_b = graph_store.fuzzy_entity_search(tenant, "DocB")
        assert len(hits_b) > 0

    def test_access_filtering(self, graph_store: Neo4jGraphStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        graph_store.initialize_tenant(tenant)

        graph_store.insert_triplet(
            tenant, "Public", "has", "Info",
            document_key="pub", subj_access=[], obj_access=[],
        )
        graph_store.insert_triplet(
            tenant, "Secret", "hides", "Data",
            document_key="priv", subj_access=[999], obj_access=[999],
        )

        # User with no access should still see public nodes (empty access_keys)
        hits = graph_store.fuzzy_entity_search(tenant, "", user_access=[1])
        names = {h["name"] for h in hits}
        assert "Public" in names
        assert "Secret" not in names

        # User with matching access sees both
        hits_with = graph_store.fuzzy_entity_search(tenant, "", user_access=[999])
        names_with = {h["name"] for h in hits_with}
        assert "Public" in names_with
        assert "Secret" in names_with

    def test_update_metadata(self, graph_store: Neo4jGraphStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        graph_store.initialize_tenant(tenant)

        graph_store.insert_triplet(
            tenant, "X", "rel", "Y",
            document_key="d1", subj_tags=["old"], subj_access=[1],
        )

        graph_store.update_metadata(
            tenant, "d1", tags=["new"], access_keys=[42]
        )

        # After update, nodes should have the new tags only
        hits = graph_store.fuzzy_entity_search(tenant, "x", tags=["new"])
        assert len(hits) > 0
