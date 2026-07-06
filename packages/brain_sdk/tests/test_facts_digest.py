"""Unit tests for community detection and the digest builder."""

from __future__ import annotations

from typing import Any

from brain_sdk.facts.digest import DigestBuilder, detect_communities
from brain_sdk.facts.models import Entity


class TestDetectCommunities:
    def test_connected_pair_shares_community(self) -> None:
        comm = detect_communities(["a", "b"], [("a", "b")])
        assert comm["a"] == comm["b"]

    def test_disconnected_entities_separate(self) -> None:
        comm = detect_communities(["a", "b"], [])
        assert comm["a"] != comm["b"]

    def test_transitive_merge(self) -> None:
        comm = detect_communities(["a", "b", "c", "z"], [("a", "b"), ("b", "c")])
        assert comm["a"] == comm["b"] == comm["c"]
        assert comm["z"] != comm["a"]

    def test_representative_is_stable_min(self) -> None:
        comm = detect_communities(["m", "a", "z"], [("m", "z"), ("a", "m")])
        assert set(comm.values()) == {"a"}  # smallest id is the representative

    def test_edges_to_unknown_ids_ignored(self) -> None:
        comm = detect_communities(["a"], [("a", "ghost")])
        assert comm == {"a": "a"}


class FakeDigestStore:
    def __init__(self, entities: list[Entity], edges: list[tuple[str, str]]) -> None:
        self._entities = entities
        self._edges = edges
        self.communities: list[dict[str, Any]] = []

    def iter_entities(self, tenant_id: str) -> list[Entity]:
        return self._entities

    def entity_relationships(self, tenant_id: str) -> list[tuple[str, str]]:
        return self._edges

    def query_claims(self, tenant_id, *, subject_id=None, limit=100, **kw):  # type: ignore[no-untyped-def]
        return [{"subject": "X", "predicate": "related_to", "object": "Y"}]

    def upsert_community(self, tenant_id: str, community_id: str, summary: str, member_ids: list[str]) -> None:
        self.communities.append({"id": community_id, "summary": summary, "members": member_ids})


class FakeLLM:
    def __init__(self, text: str = "A cluster about Acme and its subsidiaries.") -> None:
        self._text = text
        self.calls = 0

    @property
    def model(self) -> str:
        return "fake"

    def complete(self, messages, *, temperature=0.2, max_tokens=1024, json_object=False):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self._text


def _ent(name: str) -> Entity:
    return Entity.make(tenant_id="t1", canonical_name=name, type="ORG")


class TestDigestBuilder:
    def test_builds_summary_for_multi_entity_community(self) -> None:
        acme, widgets, lonely = _ent("Acme"), _ent("Widgets"), _ent("Unrelated")
        store = FakeDigestStore(
            [acme, widgets, lonely], [(acme.entity_id, widgets.entity_id)]
        )
        llm = FakeLLM()
        written = DigestBuilder(store, llm).build("t1")  # type: ignore[arg-type]

        assert written == 1  # the Acme+Widgets community; the singleton is skipped
        assert store.communities[0]["summary"] == "A cluster about Acme and its subsidiaries."
        assert set(store.communities[0]["members"]) == {acme.entity_id, widgets.entity_id}

    def test_no_entities_writes_nothing(self) -> None:
        store = FakeDigestStore([], [])
        assert DigestBuilder(store, FakeLLM()).build("t1") == 0  # type: ignore[arg-type]

    def test_empty_summary_skips_community(self) -> None:
        a, b = _ent("A"), _ent("B")
        store = FakeDigestStore([a, b], [(a.entity_id, b.entity_id)])
        written = DigestBuilder(store, FakeLLM(text="")).build("t1")  # type: ignore[arg-type]
        assert written == 0
        assert store.communities == []
