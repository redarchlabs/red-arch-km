"""Unit tests for entity/predicate resolution (fakes for store, embedder, LLM)."""

from __future__ import annotations

import json

from brain_sdk.facts.models import Entity
from brain_sdk.facts.predicates import PredicateRegistry
from brain_sdk.facts.resolution import (
    EntityResolver,
    ResolutionAction,
    ResolutionThresholds,
    decide_resolution,
    resolve_predicate,
)
from brain_sdk.llm.protocol import LLMMessage


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return 3


class FakeStore:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.aliases: dict[str, list[str]] = {}
        self.vector_hits: list[tuple[Entity, float]] = []
        self.name_hits: list[tuple[Entity, float]] = []

    def find_entities(self, tenant_id, *, name=None, embedding=None, limit=10):  # type: ignore[no-untyped-def]
        if embedding is not None:
            return list(self.vector_hits)[:limit]
        if name is not None:
            return list(self.name_hits)[:limit]
        return []

    def get_entity(self, tenant_id, entity_id):  # type: ignore[no-untyped-def]
        return self.entities.get(entity_id)

    def upsert_entities(self, tenant_id, entities):  # type: ignore[no-untyped-def]
        for e in entities:
            self.entities[e.entity_id] = e

    def add_aliases(self, tenant_id, entity_id, aliases):  # type: ignore[no-untyped-def]
        self.aliases.setdefault(entity_id, []).extend(aliases)


class FakeLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[list[LLMMessage]] = []

    @property
    def model(self) -> str:
        return "fake"

    def complete(self, messages, *, temperature=0.2, max_tokens=1024, json_object=False):  # type: ignore[no-untyped-def]
        self.calls.append(messages)
        return self._response


def _entity(name: str, typ: str = "ORG") -> Entity:
    return Entity.make(tenant_id="t1", canonical_name=name, type=typ)


class TestDecideResolution:
    def test_empty_creates_new(self) -> None:
        d = decide_resolution([], thresholds=ResolutionThresholds())
        assert d.action is ResolutionAction.CREATE_NEW

    def test_high_cosine_merges(self) -> None:
        e = _entity("Acme")
        d = decide_resolution([(e, 0.95)], thresholds=ResolutionThresholds())
        assert d.action is ResolutionAction.MERGE
        assert d.entity_id == e.entity_id

    def test_middle_band_adjudicates(self) -> None:
        e = _entity("Acme")
        d = decide_resolution([(e, 0.7)], thresholds=ResolutionThresholds())
        assert d.action is ResolutionAction.ADJUDICATE
        assert d.candidate_ids == (e.entity_id,)

    def test_lexical_extra_widens_band(self) -> None:
        d = decide_resolution([], thresholds=ResolutionThresholds(), extra_candidate_ids=("lex-1",))
        assert d.action is ResolutionAction.ADJUDICATE
        assert d.candidate_ids == ("lex-1",)

    def test_below_floor_creates_new(self) -> None:
        e = _entity("Acme")
        d = decide_resolution([(e, 0.3)], thresholds=ResolutionThresholds())
        assert d.action is ResolutionAction.CREATE_NEW


class TestEntityResolver:
    def test_new_mention_creates_entity(self) -> None:
        store = FakeStore()
        resolver = EntityResolver(store, FakeEmbedder())  # type: ignore[arg-type]
        eid = resolver.resolve("t1", "Brand New Co", "ORG")
        assert eid in store.entities
        assert store.entities[eid].canonical_name == "Brand New Co"

    def test_auto_merge_returns_existing_and_aliases(self) -> None:
        store = FakeStore()
        existing = _entity("International Business Machines")
        store.entities[existing.entity_id] = existing
        store.vector_hits = [(existing, 0.96)]
        resolver = EntityResolver(store, FakeEmbedder())  # type: ignore[arg-type]
        eid = resolver.resolve("t1", "IBM", "ORG")
        assert eid == existing.entity_id
        assert store.aliases[existing.entity_id] == ["IBM"]

    def test_adjudication_picks_candidate(self) -> None:
        store = FakeStore()
        cand = _entity("Apple Inc")
        store.entities[cand.entity_id] = cand
        store.vector_hits = [(cand, 0.7)]
        llm = FakeLLM(json.dumps({"match": cand.entity_id}))
        resolver = EntityResolver(store, FakeEmbedder(), llm=llm)  # type: ignore[arg-type]
        eid = resolver.resolve("t1", "Apple", "ORG")
        assert eid == cand.entity_id
        assert llm.calls  # the LLM was actually consulted

    def test_adjudication_none_creates_new(self) -> None:
        store = FakeStore()
        cand = _entity("Apple Inc")
        store.entities[cand.entity_id] = cand
        store.vector_hits = [(cand, 0.7)]
        llm = FakeLLM(json.dumps({"match": None}))
        resolver = EntityResolver(store, FakeEmbedder(), llm=llm)  # type: ignore[arg-type]
        eid = resolver.resolve("t1", "Apple Records", "ORG")
        assert eid != cand.entity_id
        assert eid in store.entities

    def test_type_incompatible_candidate_ignored(self) -> None:
        store = FakeStore()
        planet = _entity("Mercury", typ="PLANET")
        store.entities[planet.entity_id] = planet
        store.vector_hits = [(planet, 0.99)]  # high score but wrong type
        resolver = EntityResolver(store, FakeEmbedder())  # type: ignore[arg-type]
        eid = resolver.resolve("t1", "Mercury", "ELEMENT")
        assert eid != planet.entity_id  # created fresh, not merged across types


class TestPredicateResolution:
    def test_alias_resolves_to_canonical(self) -> None:
        reg = PredicateRegistry()
        assert resolve_predicate(reg, "is managed by") == "reports_to"

    def test_unknown_predicate_slugged(self) -> None:
        reg = PredicateRegistry()
        assert resolve_predicate(reg, "Collaborates With") == "collaborates_with"
