"""Schema-guided claim extraction.

Turns a text chunk into ``ClaimCandidate``s — subject/predicate/object with the
supporting sentence (provenance) and a confidence. Extraction is *guided* by the
predicate ontology (the model is shown the canonical vocabulary and asked to
prefer it) and a small entity-type set, so the graph stays canonical instead of
sprawling into free-form relationships.

Candidates are pre-resolution: subject/object are still surface *mentions*
(name + type), not canonical ids. The ingest pipeline resolves them to entities
and normalises predicates before building stored ``Claim``s.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from brain_sdk.facts.models import ObjectType
from brain_sdk.facts.predicates import PredicateRegistry
from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

DEFAULT_ENTITY_TYPES: tuple[str, ...] = (
    "PERSON",
    "ORG",
    "LOCATION",
    "PRODUCT",
    "EVENT",
    "CONCEPT",
    "DATE",
    "OTHER",
)

_LITERAL_TYPES = {
    "text": ObjectType.TEXT,
    "number": ObjectType.NUMBER,
    "date": ObjectType.DATE,
    "boolean": ObjectType.BOOLEAN,
}


@dataclass(frozen=True, slots=True)
class EntityMention:
    """A surface mention of an entity, pre-resolution."""

    name: str
    type: str


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    """An extracted claim before entity resolution / predicate normalisation."""

    subject: EntityMention
    predicate: str
    text_span: str
    object_entity: EntityMention | None = None
    object_value: str | None = None
    object_value_type: ObjectType = ObjectType.TEXT
    confidence: float = 0.8

    @property
    def object_is_entity(self) -> bool:
        return self.object_entity is not None


def _build_system_prompt(registry: PredicateRegistry, entity_types: tuple[str, ...]) -> str:
    vocab = ", ".join(registry.keys())
    types = ", ".join(entity_types)
    return (
        "You extract knowledge-graph claims from text. A claim is "
        "subject-predicate-object. Return ONLY a JSON object of the form "
        '{"claims": [ ... ]}. Each claim has:\n'
        '- "subject": {"name": str, "type": one of the entity types}\n'
        '- "predicate": a short verb phrase. PREFER these canonical predicates '
        f"when they fit: [{vocab}]. Otherwise use a concise snake_case phrase.\n"
        '- "object": EITHER {"entity": {"name": str, "type": ...}} when the object '
        'is a named entity, OR {"value": str, "value_type": "text|number|date|boolean"} '
        "for a literal value.\n"
        '- "evidence": the exact sentence from the text that supports the claim.\n'
        '- "confidence": a number 0..1.\n'
        f"Entity types: [{types}]. Extract only claims clearly stated in the text. "
        "If none, return an empty list."
    )


class ClaimExtractor:
    """Extract claim candidates from text via a provider-agnostic LLM."""

    def __init__(
        self,
        llm: LLMClient,
        registry: PredicateRegistry | None = None,
        *,
        entity_types: tuple[str, ...] = DEFAULT_ENTITY_TYPES,
        max_tokens: int = 1200,
    ) -> None:
        self._llm = llm
        self._registry = registry or PredicateRegistry()
        self._entity_types = entity_types
        self._max_tokens = max_tokens
        self._system = _build_system_prompt(self._registry, entity_types)

    def extract(self, text: str) -> list[ClaimCandidate]:
        if not text.strip():
            return []
        try:
            raw = self._llm.complete(
                [LLMMessage("system", self._system), LLMMessage("user", text)],
                temperature=0.1,
                max_tokens=self._max_tokens,
                json_object=True,
            )
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Claim extraction returned unparseable JSON: %s", exc)
            return []

        items = data.get("claims") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [c for item in items if (c := self._parse_candidate(item)) is not None]

    def _parse_candidate(self, item: object) -> ClaimCandidate | None:
        if not isinstance(item, dict):
            return None
        subject = self._parse_mention(item.get("subject"))
        predicate = str(item.get("predicate", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        if subject is None or not predicate:
            return None

        obj = item.get("object")
        if not isinstance(obj, dict):
            return None

        confidence = _coerce_confidence(item.get("confidence"))

        if "entity" in obj:
            object_entity = self._parse_mention(obj.get("entity"))
            if object_entity is None:
                return None
            return ClaimCandidate(
                subject=subject,
                predicate=predicate,
                text_span=evidence,
                object_entity=object_entity,
                confidence=confidence,
            )

        value = obj.get("value")
        if value is None or str(value).strip() == "":
            return None
        value_type = _LITERAL_TYPES.get(str(obj.get("value_type", "text")).lower(), ObjectType.TEXT)
        return ClaimCandidate(
            subject=subject,
            predicate=predicate,
            text_span=evidence,
            object_value=str(value).strip(),
            object_value_type=value_type,
            confidence=confidence,
        )

    def _parse_mention(self, raw: object) -> EntityMention | None:
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name", "")).strip()
        if not name:
            return None
        etype = str(raw.get("type", "OTHER")).strip().upper() or "OTHER"
        if etype not in self._entity_types:
            etype = "OTHER"
        return EntityMention(name=name, type=etype)


def _coerce_confidence(value: object) -> float:
    try:
        conf = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.8
    return max(0.0, min(1.0, conf))
