"""Unit tests for schema-guided claim extraction (canned LLM responses)."""

from __future__ import annotations

import json

from brain_sdk.facts.extraction import ClaimExtractor
from brain_sdk.facts.models import ObjectType


class FakeLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.system_prompt: str | None = None

    @property
    def model(self) -> str:
        return "fake"

    def complete(self, messages, *, temperature=0.2, max_tokens=1024, json_object=False):  # type: ignore[no-untyped-def]
        self.system_prompt = next((m.content for m in messages if m.role == "system"), None)
        return self._response


def _extractor(response: str) -> ClaimExtractor:
    return ClaimExtractor(FakeLLM(response))  # type: ignore[arg-type]


class TestExtraction:
    def test_entity_object_claim(self) -> None:
        payload = json.dumps(
            {
                "claims": [
                    {
                        "subject": {"name": "Acme", "type": "ORG"},
                        "predicate": "acquired",
                        "object": {"entity": {"name": "Widgets", "type": "ORG"}},
                        "evidence": "Acme acquired Widgets.",
                        "confidence": 0.9,
                    }
                ]
            }
        )
        [claim] = _extractor(payload).extract("Acme acquired Widgets.")
        assert claim.subject.name == "Acme"
        assert claim.predicate == "acquired"
        assert claim.object_is_entity
        assert claim.object_entity is not None
        assert claim.object_entity.name == "Widgets"
        assert claim.text_span == "Acme acquired Widgets."
        assert claim.confidence == 0.9

    def test_literal_object_claim(self) -> None:
        payload = json.dumps(
            {
                "claims": [
                    {
                        "subject": {"name": "Acme", "type": "ORG"},
                        "predicate": "headquartered in",
                        "object": {"value": "Paris", "value_type": "text"},
                        "evidence": "Acme is headquartered in Paris.",
                        "confidence": 0.75,
                    }
                ]
            }
        )
        [claim] = _extractor(payload).extract("Acme is headquartered in Paris.")
        assert not claim.object_is_entity
        assert claim.object_value == "Paris"
        assert claim.object_value_type is ObjectType.TEXT

    def test_empty_text_short_circuits(self) -> None:
        assert _extractor("{}").extract("   ") == []

    def test_malformed_json_returns_empty(self) -> None:
        assert _extractor("not json at all").extract("some text") == []

    def test_missing_subject_skipped(self) -> None:
        payload = json.dumps({"claims": [{"predicate": "acquired", "object": {"value": "x"}}]})
        assert _extractor(payload).extract("text") == []

    def test_unknown_entity_type_becomes_other(self) -> None:
        payload = json.dumps(
            {
                "claims": [
                    {
                        "subject": {"name": "Zeta", "type": "SPACESHIP"},
                        "predicate": "related_to",
                        "object": {"value": "warp", "value_type": "text"},
                        "evidence": "Zeta relates to warp.",
                    }
                ]
            }
        )
        [claim] = _extractor(payload).extract("Zeta relates to warp.")
        assert claim.subject.type == "OTHER"

    def test_confidence_clamped(self) -> None:
        payload = json.dumps(
            {
                "claims": [
                    {
                        "subject": {"name": "A", "type": "ORG"},
                        "predicate": "p",
                        "object": {"value": "v"},
                        "confidence": 5,
                    }
                ]
            }
        )
        [claim] = _extractor(payload).extract("t")
        assert claim.confidence == 1.0

    def test_system_prompt_lists_canonical_vocab(self) -> None:
        llm = FakeLLM(json.dumps({"claims": []}))
        ClaimExtractor(llm).extract("hi")  # type: ignore[arg-type]
        assert llm.system_prompt is not None
        assert "headquartered_in" in llm.system_prompt
