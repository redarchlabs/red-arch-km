"""Folder-limited graph context (real Neo4j, and Qdrant for the backfill).

``/api/vector-chat`` and ``/api/v1/ask[/stream]`` add graph context through
``fuzzy_relationship_search``. When the caller is limited to a set of folders
(``folder_tags``), a claim is returned only if ONE of its source documents is both
in those folders and readable by the caller's masks. Folder and masks are tested
on the same document: a claim stated by a restricted document inside the folders
and by a public document outside them must stay hidden.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from brain_api.services.ingest_service import IngestService
from brain_sdk.facts.models import Claim, Entity, ObjectType, Provenance
from brain_sdk.facts.neo4j_fact_store import Neo4jFactStore
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore
from brain_sdk.vector_store.protocol import VectorRecord
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore
from testcontainers.neo4j import Neo4jContainer

pytestmark = pytest.mark.integration

PUBLIC = 0
PROFILE_MASK = 1_111
HR_MASK = 2_222
PROFILE_KEYS = [PUBLIC, PROFILE_MASK]

FOLDER_A = "folder:aaaaaaaa-0000-0000-0000-000000000001"
FOLDER_B = "folder:bbbbbbbb-0000-0000-0000-000000000002"
FOLDER_C = "folder:cccccccc-0000-0000-0000-000000000003"

QUERY = "Acme headquarters salary budget lead"


@pytest.fixture(scope="module")
def fact_store(neo4j_container: Neo4jContainer) -> Generator[Neo4jFactStore]:
    store = Neo4jFactStore(neo4j_container.get_connection_url(), "neo4j", neo4j_container.password)
    store.ensure_schema()
    yield store
    store.close()


@pytest.fixture
def tenant(fact_store: Neo4jFactStore) -> Generator[str]:
    tid = "t_" + uuid.uuid4().hex[:12]
    yield tid
    fact_store.delete_tenant(tid)


@pytest.fixture
def acme(fact_store: Neo4jFactStore, tenant: str) -> Entity:
    entity = Entity.make(tenant_id=tenant, canonical_name="Acme Holdings", type="ORG")
    fact_store.upsert_entities(tenant, [entity])
    return entity


def _state(
    fact_store: Neo4jFactStore,
    tenant: str,
    subject: Entity,
    predicate: str,
    value: str,
    *,
    doc: str,
    folder: str | None,
    keys: tuple[int, ...] = (),
) -> None:
    """Ingest one claim as document ``doc`` (in ``folder``, masked by ``keys``) states it."""
    fact_store.insert_claims(
        tenant,
        [
            Claim(
                tenant_id=tenant,
                subject_id=subject.entity_id,
                predicate=predicate,
                object_type=ObjectType.TEXT,
                object_value=value,
                access_keys=keys,
                tags=(folder,) if folder else (),
                provenance=(Provenance(document_key=doc, chunk_id=f"{doc}#0", text_span="…", extractor_model="t"),),
            )
        ],
    )


def _preds(
    graph: Neo4jGraphStore,
    tenant: str,
    *,
    folders: list[str] | None,
    keys: list[int] | None = PROFILE_KEYS,
) -> set[str]:
    rows = graph.fuzzy_relationship_search(tenant, QUERY, user_access=keys, folder_tags=folders)
    return {str(r["pred"]) for r in rows}


class TestFolderScopedClaimSearch:
    def test_claim_from_a_visible_document_in_the_folders_is_returned(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-a", folder=FOLDER_A, keys=(PROFILE_MASK,))
        assert _preds(graph_store, tenant, folders=[FOLDER_A, FOLDER_B]) == {"headquarters"}

    def test_claim_only_from_outside_the_folders_is_excluded(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-a", folder=FOLDER_A)
        _state(fact_store, tenant, acme, "budget", "4M", doc="doc-c", folder=FOLDER_C)

        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == {"headquarters"}
        # Unrestricted masks do not lift the folder limit.
        assert _preds(graph_store, tenant, folders=[FOLDER_A], keys=None) == {"headquarters"}

    def test_folder_and_masks_must_hold_on_the_same_document(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        """In-folder source is HR-only; the public source is outside the folders. The
        claim's masks (the union, public) pass and its folder (via doc-hr) matches,
        yet no single document is both readable and in the folders."""
        _state(fact_store, tenant, acme, "salary", "Band 9", doc="doc-hr", folder=FOLDER_A, keys=(HR_MASK,))
        _state(fact_store, tenant, acme, "salary", "Band 9", doc="doc-pub", folder=FOLDER_C, keys=())

        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == set()
        # Without the folder limit it is visible through the public document.
        assert _preds(graph_store, tenant, folders=None) == {"salary"}
        # An HR reader limited to the folder sees it through doc-hr.
        assert _preds(graph_store, tenant, folders=[FOLDER_A], keys=[PUBLIC, HR_MASK]) == {"salary"}

    def test_untagged_document_is_excluded_until_backfilled(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-old", folder=FOLDER_A)
        # A Document node written before nodes carried tags.
        fact_store._run(  # noqa: SLF001
            f"MATCH (d:Document:{fact_store._tenant_label(tenant)} {{document_key: 'doc-old'}}) REMOVE d.tags"  # noqa: SLF001
        )
        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == set()

        fact_store.update_document_metadata(tenant, "doc-old", tags=[FOLDER_A])
        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == {"headquarters"}

    def test_no_folder_tags_is_unchanged(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-a", folder=FOLDER_A)
        _state(fact_store, tenant, acme, "budget", "4M", doc="doc-c", folder=FOLDER_C)
        _state(fact_store, tenant, acme, "salary", "Band 9", doc="doc-none", folder=None)
        graph_store.insert_triplets(tenant, [("Acme Holdings", "lead", "Dana")], document_key="doc-legacy")

        assert _preds(graph_store, tenant, folders=None) == {"headquarters", "budget", "salary", "lead"}

    def test_corroborated_claim_follows_each_source_folder(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        """Claim tags are set at creation only (folder A here); the folder-B source
        arrives by corroboration and must still count."""
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-a", folder=FOLDER_A)
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-b", folder=FOLDER_B)

        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == {"headquarters"}
        assert _preds(graph_store, tenant, folders=[FOLDER_B]) == {"headquarters"}
        assert _preds(graph_store, tenant, folders=[FOLDER_C]) == set()

    def test_folder_move_retags_the_source(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-a", folder=FOLDER_A)
        fact_store.update_document_metadata(tenant, "doc-a", tags=[FOLDER_C])

        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == set()
        assert _preds(graph_store, tenant, folders=[FOLDER_C]) == {"headquarters"}

    def test_legacy_triplets_are_skipped_when_folder_limited(self, graph_store: Neo4jGraphStore, tenant: str) -> None:
        graph_store.insert_triplets(
            tenant, [("Acme Holdings", "lead", "Dana")], document_key="doc-legacy", tags=[FOLDER_A]
        )
        assert _preds(graph_store, tenant, folders=[FOLDER_A]) == set()
        assert _preds(graph_store, tenant, folders=None) == {"lead"}

    def test_explicit_empty_folder_list_returns_nothing(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, acme: Entity
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-a", folder=FOLDER_A)
        assert _preds(graph_store, tenant, folders=[]) == set()


class TestBackfillFromTheVectorStore:
    def test_backfill_restores_folder_scoped_visibility(
        self,
        fact_store: Neo4jFactStore,
        graph_store: Neo4jGraphStore,
        vector_store: QdrantVectorStore,
        tenant: str,
        acme: Entity,
    ) -> None:
        _state(fact_store, tenant, acme, "headquarters", "Lisbon", doc="doc-old", folder=FOLDER_A)
        fact_store._run(  # noqa: SLF001
            f"MATCH (d:Document:{fact_store._tenant_label(tenant)}) REMOVE d.tags, d.access_keys"  # noqa: SLF001
        )
        # The document has since moved to folder B; chunk payloads carry that.
        vector_store.ensure_collections(tenant)
        vector_store.upsert_vectors(
            tenant,
            [
                VectorRecord(
                    id=str(uuid.uuid4()),
                    vector=[0.5, 0.5, 0.5, 0.5],
                    payload={
                        "text": "t",
                        "chunk_order": order,
                        "document_key": "doc-old",
                        "tenant_id": tenant,
                        "tags": [FOLDER_B],
                        "access_keys": [PUBLIC],
                        "type": "chunk",
                    },
                )
                for order in (0, 1)
            ],
        )
        try:
            assert _preds(graph_store, tenant, folders=[FOLDER_B]) == set()

            stores = MagicMock()
            stores.settings.use_fact_engine = True
            stores.vector = vector_store
            stores.fact_store = fact_store
            result = IngestService(stores).backfill_document_graph_metadata(tenant)

            assert result["documents"] == 1
            assert _preds(graph_store, tenant, folders=[FOLDER_B]) == {"headquarters"}
            assert _preds(graph_store, tenant, folders=[FOLDER_A]) == set()
        finally:
            vector_store.delete_tenant(tenant)
