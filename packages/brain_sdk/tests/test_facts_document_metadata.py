"""Document nodes carry their document's folder tags and masks.

Folder-limited graph search tests the folder on the claim's SOURCE documents
(claim ``tags`` are fixed at creation and never follow a corroborating document or
a folder move), so every write that knows a document's tags must keep them on its
``:Document`` node. Neo4j is stubbed; the Cypher runs for real in the brain_api
integration suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from brain_sdk.facts.models import Claim, ObjectType, Provenance
from brain_sdk.facts.neo4j_fact_store import Neo4jFactStore

TENANT = "org-1"


@pytest.fixture
def store() -> Iterator[Neo4jFactStore]:
    s = Neo4jFactStore("bolt://localhost:7687", "neo4j", "unused")
    yield s
    s.close()


def _claim(*, tags: tuple[str, ...], keys: tuple[int, ...]) -> Claim:
    return Claim(
        tenant_id=TENANT,
        subject_id="e1",
        predicate="hq",
        object_type=ObjectType.TEXT,
        object_value="Lisbon",
        tags=tags,
        access_keys=keys,
        provenance=(Provenance(document_key="doc-1", chunk_id="doc-1#0", text_span="…", extractor_model="t"),),
    )


class TestProvenanceWritesDocumentTags:
    def test_document_node_gets_the_claims_document_tags(self) -> None:
        tx = MagicMock()
        Neo4jFactStore._attach_provenance(tx, _claim(tags=("folder:f1", "policy"), keys=(5,)), "Tenant_org_1")

        query, params = tx.run.call_args_list[0].args[0], tx.run.call_args_list[0].kwargs
        assert "d.tags = $doc_tags" in query
        assert params["doc_tags"] == ["folder:f1", "policy"]
        assert params["doc_keys"] == [5]

    def test_tags_are_never_interpolated(self) -> None:
        tx = MagicMock()
        Neo4jFactStore._attach_provenance(tx, _claim(tags=("folder:`) DETACH DELETE n //",), keys=()), "Tenant_org_1")
        for call in tx.run.call_args_list:
            assert "DETACH DELETE" not in call.args[0]


class TestUpdateDocumentMetadata:
    @staticmethod
    def _capture(store: Neo4jFactStore, ids: list[str] | None = None) -> list[tuple[str, dict[str, Any]]]:
        calls: list[tuple[str, dict[str, Any]]] = []

        def fake_run(query: str, **params: Any) -> list[dict[str, Any]]:
            calls.append((query, params))
            if "collect(DISTINCT c.claim_id)" in query:
                return [{"ids": ids or []}]
            return [{"updated": len(ids or [])}]

        store._run = fake_run  # type: ignore[method-assign]
        return calls

    def test_tags_only_sets_document_tags_without_touching_masks(self, store: Neo4jFactStore) -> None:
        calls = self._capture(store, ids=["c1"])
        assert store.update_document_metadata(TENANT, "doc-1", tags=["folder:f2"]) == 0

        assert len(calls) == 1
        query, params = calls[0]
        assert "d.tags = $tags" in query and "access_keys" not in query
        assert params == {"dk": "doc-1", "tags": ["folder:f2"]}

    def test_access_keys_recompute_the_documents_claims(self, store: Neo4jFactStore) -> None:
        calls = self._capture(store, ids=["c1", "c2"])
        assert store.update_document_metadata(TENANT, "doc-1", tags=["folder:f2"], access_keys=[9]) == 2

        query, params = calls[0]
        assert "d.tags = $tags" in query and "d.access_keys = $keys" in query
        assert params["tags"] == ["folder:f2"] and params["keys"] == [9]
        assert calls[1][1]["ids"] == ["c1", "c2"]  # mask recompute over the supported claims

    def test_nothing_to_set_is_a_no_op(self, store: Neo4jFactStore) -> None:
        calls = self._capture(store)
        assert store.update_document_metadata(TENANT, "doc-1") == 0
        assert calls == []

    def test_update_document_access_keys_is_the_masks_only_form(self, store: Neo4jFactStore) -> None:
        calls = self._capture(store, ids=["c1"])
        assert store.update_document_access_keys(TENANT, "doc-1", [3]) == 1
        query, params = calls[0]
        assert "d.access_keys = $keys" in query and "d.tags" not in query
        assert params["keys"] == [3]
