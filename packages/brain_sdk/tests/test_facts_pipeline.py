"""Unit tests for the fact-ingest pipeline (fakes for each stage)."""

from __future__ import annotations

from brain_sdk.facts.extraction import ClaimCandidate, EntityMention
from brain_sdk.facts.models import Claim, ObjectType
from brain_sdk.facts.pipeline import Chunk, FactIngestPipeline


class FakeExtractor:
    def __init__(self, by_text: dict[str, list[ClaimCandidate]]) -> None:
        self._by_text = by_text

    def extract(self, text: str) -> list[ClaimCandidate]:
        return self._by_text.get(text, [])


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def resolve(self, tenant_id: str, name: str, entity_type: str) -> str:
        self.calls.append((name, entity_type))
        return "eid:" + name.lower().replace(" ", "_")


class FakeStore:
    def __init__(self) -> None:
        self.purged: list[str] = []
        self.inserted: list[Claim] = []

    def delete_by_document_key(self, tenant_id: str, document_key: str) -> None:
        self.purged.append(document_key)

    def insert_claims(self, tenant_id: str, claims: list[Claim]) -> dict[str, int]:
        self.inserted.extend(claims)
        return {"created": len(claims), "corroborated": 0, "superseded": 0, "contradicted": 0}


def _pipeline(extractor: FakeExtractor, resolver: FakeResolver, store: FakeStore) -> FactIngestPipeline:
    return FactIngestPipeline(extractor, resolver, store)  # type: ignore[arg-type]


class TestPipeline:
    def test_builds_entity_and_literal_claims(self) -> None:
        text = "Acme acquired Widgets. Acme is in Paris."
        candidates = [
            ClaimCandidate(
                subject=EntityMention("Acme", "ORG"),
                predicate="acquired",
                text_span="Acme acquired Widgets.",
                object_entity=EntityMention("Widgets", "ORG"),
                confidence=0.9,
            ),
            ClaimCandidate(
                subject=EntityMention("Acme", "ORG"),
                predicate="is managed by",  # alias → should normalise to reports_to
                text_span="…",
                object_value="Paris",
                object_value_type=ObjectType.TEXT,
                confidence=0.7,
            ),
        ]
        store = FakeStore()
        resolver = FakeResolver()
        pipe = _pipeline(FakeExtractor({text: candidates}), resolver, store)

        counts = pipe.ingest_document("t1", "docA", [Chunk("docA#0", text)])

        assert counts["claims_extracted"] == 2
        assert store.purged == ["docA"]  # idempotent purge-before-insert
        assert len(store.inserted) == 2

        entity_claim = next(c for c in store.inserted if c.object_type is ObjectType.ENTITY)
        assert entity_claim.subject_id == "eid:acme"
        assert entity_claim.object_id == "eid:widgets"
        assert entity_claim.predicate == "acquired"

        literal_claim = next(c for c in store.inserted if c.object_type is ObjectType.TEXT)
        assert literal_claim.object_value == "Paris"
        assert literal_claim.predicate == "reports_to"  # alias normalised
        assert literal_claim.provenance[0].chunk_id == "docA#0"
        assert literal_claim.provenance[0].document_key == "docA"

    def test_no_candidates_still_purges(self) -> None:
        store = FakeStore()
        pipe = _pipeline(FakeExtractor({}), FakeResolver(), store)
        counts = pipe.ingest_document("t1", "docB", [Chunk("docB#0", "nothing extractable")])
        assert counts["claims_extracted"] == 0
        assert store.purged == ["docB"]

    def test_replace_false_skips_purge(self) -> None:
        store = FakeStore()
        pipe = _pipeline(FakeExtractor({}), FakeResolver(), store)
        pipe.ingest_document("t1", "docC", [Chunk("docC#0", "x")], replace=False)
        assert store.purged == []
