"""Unit tests for Neo4jGraphStore.fuzzy_relationship_search.

Covers the two bugs that made graph context silently empty for every org ingested
after the fact-engine cutover:

1. the read path only knew the legacy ``(:Entity)-[:REL]->(:Entity)`` shape, never
   the reified ``(:Entity)-[:SUBJECT]->(:Claim)`` one the fact engine writes;
2. the whole query sentence was matched as one literal string, so a natural-language
   question could not match a triplet field.

Neo4j itself is stubbed — these assert the query construction and merge logic. The
Cypher is exercised against a real database in the integration suite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore

TENANT = "e7170490-02cf-410e-9f64-cb1db279668e"


@pytest.fixture
def store() -> Neo4jGraphStore:
    with patch("brain_sdk.graph_store.neo4j_store.GraphDatabase.driver", return_value=MagicMock()):
        return Neo4jGraphStore("bolt://stub", "neo4j", "pw")


class TestSearchTerms:
    def test_splits_a_question_into_content_words(self, store: Neo4jGraphStore) -> None:
        assert store._search_terms("What does it teach about the Fall of Adam and Eve?") == [
            "teach",
            "fall",
            "adam",
            "eve",
        ]

    def test_drops_stopwords_and_short_tokens(self, store: Neo4jGraphStore) -> None:
        assert store._search_terms("who is in the ark") == ["ark"]

    def test_deduplicates_while_keeping_order(self, store: Neo4jGraphStore) -> None:
        assert store._search_terms("Adam and Eve and Adam") == ["adam", "eve"]

    def test_falls_back_to_raw_term_when_everything_is_filtered(self, store: Neo4jGraphStore) -> None:
        """A stopword-only or very short query degrades to the old literal
        behaviour rather than matching nothing at all."""
        assert store._search_terms("Ur") == ["ur"]
        assert store._search_terms("the") == ["the"]

    def test_empty_query_yields_no_terms(self, store: Neo4jGraphStore) -> None:
        assert store._search_terms("   ") == []


class TestFuzzyRelationshipSearch:
    def test_finds_reified_claims_from_a_natural_language_question(self, store: Neo4jGraphStore) -> None:
        """The original bug: this returned [] because the read path only matched
        legacy REL edges and required the whole sentence verbatim."""
        claim_params: dict[str, Any] = {}

        def fake_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
            if "SUBJECT" not in query:
                return []
            claim_params.update(params)
            return [{"subj": "The Fall of Adam and Eve", "pred": "emphasizes", "obj": "growth", "score": 3}]

        store._cypher = fake_cypher  # type: ignore[method-assign]
        hits = store.fuzzy_relationship_search(TENANT, "What does it teach about the Fall of Adam and Eve?")

        assert hits == [{"subj": "The Fall of Adam and Eve", "pred": "emphasizes", "obj": "growth"}]
        assert claim_params["terms"] == ["teach", "fall", "adam", "eve"]

    def test_queries_the_claim_shape_scoped_to_the_tenant_label(self, store: Neo4jGraphStore) -> None:
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam")

        claim_query = store._cypher.call_args_list[0].args[0]
        assert "-[:SUBJECT]->(c:Claim:Tenant_e7170490_02cf_410e_9f64_cb1db279668e)" in claim_query
        assert "OPTIONAL MATCH (c)-[:OBJECT]->" in claim_query

    def test_excludes_non_active_claims(self, store: Neo4jGraphStore) -> None:
        """Superseded/contradicted claims are kept for history; feeding them to the
        model would present retracted facts as current."""
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam")

        assert "c.status = 'active'" in store._cypher.call_args_list[0].args[0]

    def test_still_searches_legacy_triplets(self, store: Neo4jGraphStore) -> None:
        """Orgs ingested before the fact engine — and anything written by
        insert_triplet — only exist in the REL shape."""

        def fake_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
            if "SUBJECT" in query:
                return []
            return [{"subj": "Alice", "pred": "knows", "obj": "Bob"}]

        store._cypher = fake_cypher  # type: ignore[method-assign]
        assert store.fuzzy_relationship_search(TENANT, "alice") == [{"subj": "Alice", "pred": "knows", "obj": "Bob"}]

    def test_ranks_by_number_of_matching_terms(self, store: Neo4jGraphStore) -> None:
        def fake_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
            if "SUBJECT" in query:
                return [
                    {"subj": "Abraham", "pred": "is", "obj": "prophet", "score": 1},
                    {"subj": "Adam and Eve", "pred": "left", "obj": "Eden", "score": 3},
                ]
            return []

        store._cypher = fake_cypher  # type: ignore[method-assign]
        hits = store.fuzzy_relationship_search(TENANT, "Adam and Eve in Eden")
        assert [h["subj"] for h in hits] == ["Adam and Eve", "Abraham"]

    def test_weights_rare_terms_above_common_ones(self, store: Neo4jGraphStore) -> None:
        """Legacy path, IDF end to end. "come"/"follow" name the corpus and appear
        everywhere; "adam"/"eve" are rare and are what the question is actually
        about. Equal-weight counting ranked the ubiquitous rows first."""
        common = [{"subj": "Come, Follow Me", "pred": "requires", "obj": f"teachers to do thing {i}"} for i in range(8)]
        rare = [{"subj": "The Fall of Adam and Eve", "pred": "emphasizes", "obj": "growth"}]

        store._cypher = lambda query, **params: [] if "SUBJECT" in query else common + rare  # type: ignore[method-assign]
        hits = store.fuzzy_relationship_search(TENANT, "What does Come, Follow Me teach about Adam and Eve?")

        assert hits[0]["subj"] == "The Fall of Adam and Eve"

    def test_merges_duplicates_across_both_shapes(self, store: Neo4jGraphStore) -> None:
        def fake_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
            row = {"subj": "Alice", "pred": "knows", "obj": "Bob"}
            return [{**row, "score": 2}] if "SUBJECT" in query else [row]

        store._cypher = fake_cypher  # type: ignore[method-assign]
        assert store.fuzzy_relationship_search(TENANT, "alice knows bob") == [
            {"subj": "Alice", "pred": "knows", "obj": "Bob"}
        ]

    def test_score_is_not_leaked_to_callers(self, store: Neo4jGraphStore) -> None:
        """_format_context renders subj/pred/obj; an extra key would be dead weight
        in the prompt payload."""

        def fake_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
            if "SUBJECT" in query:
                return [{"subj": "Adam", "pred": "left", "obj": "Eden", "score": 2}]
            return []

        store._cypher = fake_cypher  # type: ignore[method-assign]
        assert "score" not in store.fuzzy_relationship_search(TENANT, "Adam Eden")[0]

    def test_empty_query_short_circuits_without_touching_neo4j(self, store: Neo4jGraphStore) -> None:
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        assert store.fuzzy_relationship_search(TENANT, "  ") == []
        store._cypher.assert_not_called()

    def test_access_keys_and_tags_are_passed_as_parameters(self, store: Neo4jGraphStore) -> None:
        """Tenant label aside, nothing user-supplied may reach Cypher as literal text."""
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam", tags=["t1"], user_access=[7])

        params = store._cypher.call_args_list[0].kwargs
        assert params["tags"] == ["t1"]
        assert params["keys"] == [7]


class TestFolderScopedSearch:
    """``folder_tags`` limits graph context to claims stated by a document in one of
    those folders that the caller may also read.

    Claim ``tags`` are written once, when the claim is created, and never follow a
    corroborating document or a folder move — so the folder test runs against the
    source Document nodes, and the folder and the access masks are checked on the
    SAME document. Checking them on different documents would let a claim through
    because a public document outside the folders states it.
    """

    FOLDERS = ["folder:aaaa", "folder:bbbb"]

    @staticmethod
    def _queries(store: Neo4jGraphStore) -> list[str]:
        return [c.args[0] for c in store._cypher.call_args_list]  # type: ignore[attr-defined]

    def test_folder_tags_are_parameters_not_query_text(self, store: Neo4jGraphStore) -> None:
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam", user_access=[7], folder_tags=self.FOLDERS)

        query = self._queries(store)[0]
        params = store._cypher.call_args_list[0].kwargs
        assert params["folder_tags"] == self.FOLDERS
        assert all(tag not in query for tag in self.FOLDERS)

    def test_folder_and_mask_are_checked_on_the_same_source_document(self, store: Neo4jGraphStore) -> None:
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam", user_access=[7], folder_tags=self.FOLDERS)

        query = " ".join(self._queries(store)[0].split())
        label = "Tenant_e7170490_02cf_410e_9f64_cb1db279668e"
        exists_start = query.index("EXISTS {")
        exists = query[exists_start : query.index("}", query.index("WHERE", exists_start))]
        assert f"(c)-[:SOURCED_FROM]->(:Chunk:{label})-[:PART_OF]->(d:Document:{label})" in exists
        assert "$folder_tags" in exists and "d.tags" in exists
        # The mask test sits inside the same EXISTS, bound to the same d.
        assert "d.access_keys" in exists and "$keys" in exists

    def test_unrestricted_caller_still_needs_a_document_in_the_folders(self, store: Neo4jGraphStore) -> None:
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam", user_access=None, folder_tags=self.FOLDERS)

        query = self._queries(store)[0]
        assert "EXISTS {" in query and "$folder_tags" in query
        assert "d.access_keys" not in query
        assert "keys" not in store._cypher.call_args_list[0].kwargs

    def test_legacy_triplets_are_skipped_when_folder_limited(self, store: Neo4jGraphStore) -> None:
        """Legacy REL triplets carry merged entity-level tags/masks with no per-document
        provenance, so they cannot be held to the same-document rule."""

        def fake_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
            if "SUBJECT" in query:
                return []
            return [{"subj": "Alice", "pred": "knows", "obj": "Bob"}]

        store._cypher = MagicMock(side_effect=fake_cypher)  # type: ignore[method-assign]
        assert store.fuzzy_relationship_search(TENANT, "alice", folder_tags=self.FOLDERS) == []
        assert all("SUBJECT" in q for q in self._queries(store))

    def test_explicit_empty_folder_list_matches_nothing(self, store: Neo4jGraphStore) -> None:
        """An empty list is a folder limit with no folders in it — never 'unlimited'."""
        store._cypher = MagicMock(return_value=[{"subj": "A", "pred": "b", "obj": "c", "score": 1}])  # type: ignore[method-assign]
        assert store.fuzzy_relationship_search(TENANT, "Adam", folder_tags=[]) == []
        store._cypher.assert_not_called()

    def test_no_folder_tags_leaves_the_query_unchanged(self, store: Neo4jGraphStore) -> None:
        store._cypher = MagicMock(return_value=[])  # type: ignore[method-assign]
        store.fuzzy_relationship_search(TENANT, "Adam", user_access=[7])

        queries = self._queries(store)
        assert len(queries) == 2  # claim shape + legacy shape
        assert "EXISTS" not in queries[0] and "Document" not in queries[0]
        assert "folder_tags" not in store._cypher.call_args_list[0].kwargs
