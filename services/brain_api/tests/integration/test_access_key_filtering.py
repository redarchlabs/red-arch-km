"""The stores filter on exactly the masks the API hands them (real Qdrant + Neo4j).

The API resolves a requester — a member, an agent's actor, or an API key scoped to
dimension assignments — into a list of masks and passes it to brain-api. These pin the other
half of that contract: given a restricted profile's masks, neither the passage
store nor the fact graph returns anything from a document whose masks do not
overlap them, while public content (stored as the ``0`` sentinel in Qdrant and as
an empty key list on claims) stays visible.

The masks are illustrative integers, not encoded dimensions: both stores match
by plain set membership, so the encoding is irrelevant here.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from access_mask import MAX_GROUP, MAX_REGION, MAX_ROLE, encode, expand_member_masks
from brain_sdk.facts.models import Claim, Entity, ObjectType, Provenance
from brain_sdk.facts.neo4j_fact_store import Neo4jFactStore
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore
from brain_sdk.vector_store.protocol import VectorRecord
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore
from testcontainers.neo4j import Neo4jContainer

pytestmark = pytest.mark.integration

PUBLIC = 0  # the sentinel ingest writes for an unrestricted document's chunks
PROFILE_MASK = 1_111  # what a restricted profile asserts
HR_MASK = 2_222  # a folder the profile cannot see
PROFILE_KEYS = [PUBLIC, PROFILE_MASK]


def _chunk(tenant: str, doc_key: str, access_keys: list[int], vector: list[float]) -> VectorRecord:
    return VectorRecord(
        id=str(uuid.uuid4()),
        vector=vector,
        payload={
            "text": f"{doc_key} text",
            "chunk_order": 0,
            "document_key": doc_key,
            "document_title": doc_key,
            "tenant_id": tenant,
            "tags": [],
            "access_keys": access_keys,
            "type": "chunk",
        },
    )


class TestPassageStore:
    def test_restricted_chunks_never_reach_a_profile_without_the_mask(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)
        query = [0.5, 0.5, 0.5, 0.5]
        vector_store.upsert_vectors(
            tenant,
            [
                _chunk(tenant, "public-doc", [PUBLIC], query),
                _chunk(tenant, "project-doc", [PROFILE_MASK], query),
                _chunk(tenant, "hr-doc", [HR_MASK], query),
            ],
        )

        seen = {
            r.payload["document_key"]
            for r in vector_store.search(tenant_id=tenant, query_vector=query, limit=10, access_keys=PROFILE_KEYS)
        }
        assert seen == {"public-doc", "project-doc"}

        # Sibling expansion (used to widen the top document) applies the same filter.
        assert (
            vector_store.list_document_chunks(
                tenant_id=tenant, document_key="hr-doc", limit=10, access_keys=PROFILE_KEYS
            )
            == []
        )

        # Unrestricted (None) is what an org admin / unbound org key gets.
        everything = {
            r.payload["document_key"]
            for r in vector_store.search(tenant_id=tenant, query_vector=query, limit=10, access_keys=None)
        }
        assert everything == {"public-doc", "project-doc", "hr-doc"}
        vector_store.delete_tenant(tenant)


@pytest.fixture(scope="module")
def fact_store(neo4j_container: Neo4jContainer) -> Generator[Neo4jFactStore]:
    store = Neo4jFactStore(neo4j_container.get_connection_url(), "neo4j", neo4j_container.password)
    store.ensure_schema()
    yield store
    store.close()


def _claim(tenant: str, subj: Entity, predicate: str, obj: str, doc_key: str, keys: tuple[int, ...]) -> Claim:
    return Claim(
        tenant_id=tenant,
        subject_id=subj.entity_id,
        predicate=predicate,
        object_type=ObjectType.TEXT,
        object_value=obj,
        access_keys=keys,
        provenance=(Provenance(document_key=doc_key, chunk_id=f"{doc_key}#0", text_span="…", extractor_model="test"),),
    )


class TestFactStore:
    @pytest.fixture
    def tenant(self, fact_store: Neo4jFactStore) -> Generator[str]:
        tid = "t_" + uuid.uuid4().hex[:12]
        yield tid
        fact_store.delete_tenant(tid)

    @pytest.fixture
    def seeded(self, fact_store: Neo4jFactStore, tenant: str) -> Entity:
        acme = Entity.make(tenant_id=tenant, canonical_name="Acme Holdings", type="ORG")
        fact_store.upsert_entities(tenant, [acme])
        fact_store.insert_claims(
            tenant,
            [
                _claim(tenant, acme, "headquartered_in", "Lisbon", "public-doc", ()),
                _claim(tenant, acme, "project_lead", "Dana", "project-doc", (PROFILE_MASK,)),
                _claim(tenant, acme, "salary_band", "Band 9", "hr-doc", (HR_MASK,)),
            ],
        )
        return acme

    def test_query_claims_hides_facts_from_unseen_folders(
        self, fact_store: Neo4jFactStore, tenant: str, seeded: Entity
    ) -> None:
        rows = fact_store.query_claims(tenant, subject_id=seeded.entity_id, access_keys=PROFILE_KEYS)
        assert {r["predicate"] for r in rows} == {"headquartered_in", "project_lead"}

        everything = fact_store.query_claims(tenant, subject_id=seeded.entity_id, access_keys=None)
        assert {r["predicate"] for r in everything} == {"headquartered_in", "project_lead", "salary_band"}

    def test_neighborhood_hides_facts_from_unseen_folders(
        self, fact_store: Neo4jFactStore, tenant: str, seeded: Entity
    ) -> None:
        rows = fact_store.neighborhood(tenant, seeded.entity_id, access_keys=PROFILE_KEYS)
        assert "salary_band" not in {r["predicate"] for r in rows}
        assert "Band 9" not in {str(r.get("object")) for r in rows}

    def test_rag_graph_context_hides_facts_from_unseen_folders(
        self, fact_store: Neo4jFactStore, graph_store: Neo4jGraphStore, tenant: str, seeded: Entity
    ) -> None:
        """``/api/vector-chat`` (what v1 search/chat calls) adds graph context via
        ``fuzzy_relationship_search``, which reads the same reified claims."""
        rows = graph_store.fuzzy_relationship_search(tenant, "Acme salary band lead", user_access=PROFILE_KEYS)
        preds = {r["pred"] for r in rows}
        assert "salary_band" not in preds
        assert "project_lead" in preds

        unrestricted = graph_store.fuzzy_relationship_search(tenant, "Acme salary band lead", user_access=None)
        assert "salary_band" in {r["pred"] for r in unrestricted}


# ---- wildcard folder masks ---------------------------------------------------
#
# A folder configured as {"department": "Finance"} carries a mask whose other
# dimensions are their wildcard (MAX). Stores compare masks by equality, so this
# only works because member masks are expanded with wildcard variants
# (access_mask.expand_member_masks) before they are sent.

ORG = 77
FINANCE, HR = 5, 6
WEST, EAST = 3, 4
FINANCE_ANY_REGION = encode(org=ORG, region=MAX_REGION, dept=FINANCE, role=MAX_ROLE, group=MAX_GROUP)
FINANCE_WEST_ONLY = encode(org=ORG, region=WEST, dept=FINANCE, role=MAX_ROLE, group=MAX_GROUP)


def _member(region: int, dept: int) -> list[int]:
    return [PUBLIC, *expand_member_masks([encode(org=ORG, region=region, dept=dept)])]


FINANCE_WEST = _member(WEST, FINANCE)
FINANCE_EAST = _member(EAST, FINANCE)
HR_WEST = _member(WEST, HR)


class TestWildcardMasksInThePassageStore:
    def test_partially_scoped_folders(self, vector_store: QdrantVectorStore) -> None:
        tenant = f"t-{uuid.uuid4().hex[:8]}"
        vector_store.ensure_collections(tenant)
        q = [0.5, 0.5, 0.5, 0.5]
        vector_store.upsert_vectors(
            tenant,
            [
                _chunk(tenant, "finance-doc", [FINANCE_ANY_REGION], q),
                _chunk(tenant, "finance-west-doc", [FINANCE_WEST_ONLY], q),
            ],
        )

        def seen(keys: list[int]) -> set[str]:
            hits = vector_store.search(tenant_id=tenant, query_vector=q, limit=10, access_keys=keys)
            return {r.payload["document_key"] for r in hits}

        assert seen(FINANCE_WEST) == {"finance-doc", "finance-west-doc"}
        assert seen(FINANCE_EAST) == {"finance-doc"}  # wrong region for the region-scoped one
        assert seen(HR_WEST) == set()  # wrong department
        vector_store.delete_tenant(tenant)


class TestWildcardMasksInTheFactStore:
    def test_partially_scoped_claims(self, fact_store: Neo4jFactStore) -> None:
        tenant = "t_" + uuid.uuid4().hex[:12]
        try:
            acme = Entity.make(tenant_id=tenant, canonical_name="Acme Holdings", type="ORG")
            fact_store.upsert_entities(tenant, [acme])
            fact_store.insert_claims(
                tenant,
                [
                    _claim(tenant, acme, "budget_owner", "Lee", "finance-doc", (FINANCE_ANY_REGION,)),
                    _claim(tenant, acme, "west_budget", "4M", "finance-west-doc", (FINANCE_WEST_ONLY,)),
                ],
            )

            def preds(keys: list[int]) -> set[str]:
                return {
                    r["predicate"] for r in fact_store.query_claims(tenant, subject_id=acme.entity_id, access_keys=keys)
                }

            assert preds(FINANCE_WEST) == {"budget_owner", "west_budget"}
            assert preds(FINANCE_EAST) == {"budget_owner"}
            assert preds(HR_WEST) == set()
        finally:
            fact_store.delete_tenant(tenant)


class TestClaimMasksAreTheUnionOfTheirSources:
    """A fact is visible to whoever can see at least one document that states it.

    So a claim's ``access_keys`` are the union of its source documents' masks — and
    public if any source is public — recomputed whenever a source is added,
    removed, moved or re-masked.
    """

    @pytest.fixture
    def tenant(self, fact_store: Neo4jFactStore) -> Generator[str]:
        tid = "t_" + uuid.uuid4().hex[:12]
        yield tid
        fact_store.delete_tenant(tid)

    @staticmethod
    def _seen(fact_store: Neo4jFactStore, tenant: str, subject: Entity, keys: list[int]) -> set[str]:
        rows = fact_store.query_claims(tenant, subject_id=subject.entity_id, access_keys=keys)
        return {r["predicate"] for r in rows}

    def _acme(self, fact_store: Neo4jFactStore, tenant: str) -> Entity:
        acme = Entity.make(tenant_id=tenant, canonical_name="Acme Holdings", type="ORG")
        fact_store.upsert_entities(tenant, [acme])
        return acme

    def test_public_then_restricted_source_then_public_deleted(self, fact_store: Neo4jFactStore, tenant: str) -> None:
        acme = self._acme(fact_store, tenant)
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "salary_band", "Band 9", "public-doc", ())])
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "salary_band", "Band 9", "hr-doc", (HR_MASK,))])
        # Still stated by a public document: everyone sees it.
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == {"salary_band"}

        fact_store.delete_by_document_key(tenant, "public-doc")

        # Only the HR document states it now.
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == set()
        assert self._seen(fact_store, tenant, acme, [PUBLIC, HR_MASK]) == {"salary_band"}

    def test_restricted_then_public_source_makes_it_public(self, fact_store: Neo4jFactStore, tenant: str) -> None:
        acme = self._acme(fact_store, tenant)
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "hq", "Lisbon", "hr-doc", (HR_MASK,))])
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == set()

        fact_store.insert_claims(tenant, [_claim(tenant, acme, "hq", "Lisbon", "public-doc", ())])

        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == {"hq"}

    def test_two_restricted_sources_union(self, fact_store: Neo4jFactStore, tenant: str) -> None:
        acme = self._acme(fact_store, tenant)
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "budget", "4M", "hr-doc", (HR_MASK,))])
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "budget", "4M", "project-doc", (PROFILE_MASK,))])

        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == {"budget"}
        assert self._seen(fact_store, tenant, acme, [PUBLIC, HR_MASK]) == {"budget"}
        assert self._seen(fact_store, tenant, acme, [PUBLIC, 9_999]) == set()

    def test_moving_a_single_source_re_masks_the_claim(self, fact_store: Neo4jFactStore, tenant: str) -> None:
        acme = self._acme(fact_store, tenant)
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "salary_band", "Band 9", "moved-doc", ())])
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == {"salary_band"}

        fact_store.update_document_access_keys(tenant, "moved-doc", [HR_MASK])

        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == set()
        assert self._seen(fact_store, tenant, acme, [HR_MASK]) == {"salary_band"}

    def test_moving_one_of_two_sources_recomputes_the_union(self, fact_store: Neo4jFactStore, tenant: str) -> None:
        acme = self._acme(fact_store, tenant)
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "salary_band", "Band 9", "hr-doc", (HR_MASK,))])
        fact_store.insert_claims(tenant, [_claim(tenant, acme, "salary_band", "Band 9", "moved-doc", (HR_MASK,))])
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == set()

        # The second source moves to a folder the profile can see: the fact is now
        # stated somewhere the profile may read.
        fact_store.update_document_access_keys(tenant, "moved-doc", [PROFILE_MASK])
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == {"salary_band"}

        # And back: the union shrinks again.
        fact_store.update_document_access_keys(tenant, "moved-doc", [HR_MASK])
        assert self._seen(fact_store, tenant, acme, PROFILE_KEYS) == set()
