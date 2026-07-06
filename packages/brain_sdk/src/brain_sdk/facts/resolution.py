"""Entity and predicate resolution — collapsing surface forms to canonical nodes.

Entity resolution is the prerequisite that makes "IBM" / "I.B.M." /
"International Business Machines" one node before any claim is written. Without
it the graph fragments and downstream traversal/aggregation breaks.

Strategy (blocking → threshold → adjudication):

1. **Block** — embed the mention and pull nearest existing entities by vector
   similarity, plus lexical name matches, scoped to type-compatible candidates.
2. **Decide** (pure, unit-tested) — high cosine → auto-merge; a middle band →
   ask an LLM to adjudicate; nothing close → create a new canonical entity.
3. **Adjudicate** — the LLM picks the true match among the ambiguous band, or
   "none" (→ create new).

Predicate resolution is a thin wrapper over the predicate ontology.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum

from brain_sdk.embedding.protocol import EmbeddingProvider
from brain_sdk.facts.models import Entity
from brain_sdk.facts.predicates import PredicateRegistry
from brain_sdk.facts.protocol import FactStore
from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


class ResolutionAction(StrEnum):
    MERGE = "merge"
    ADJUDICATE = "adjudicate"
    CREATE_NEW = "create_new"


@dataclass(frozen=True, slots=True)
class ResolutionThresholds:
    """Cosine bands that route a mention to merge / adjudicate / create."""

    auto_merge: float = 0.90
    adjudicate_floor: float = 0.62


@dataclass(frozen=True, slots=True)
class ResolutionDecision:
    action: ResolutionAction
    entity_id: str | None = None
    candidate_ids: tuple[str, ...] = ()


def decide_resolution(
    vector_candidates: list[tuple[Entity, float]],
    *,
    thresholds: ResolutionThresholds,
    extra_candidate_ids: tuple[str, ...] = (),
) -> ResolutionDecision:
    """Pure banding decision.

    ``vector_candidates`` are ``(entity, cosine)`` pairs, best first, already
    filtered to type-compatible entities. ``extra_candidate_ids`` widen the
    adjudication set with lexical matches that fell below the vector floor.
    """
    if vector_candidates:
        top_entity, top_score = vector_candidates[0]
        if top_score >= thresholds.auto_merge:
            return ResolutionDecision(ResolutionAction.MERGE, entity_id=top_entity.entity_id)

    band = [e.entity_id for e, s in vector_candidates if s >= thresholds.adjudicate_floor]
    for cid in extra_candidate_ids:
        if cid not in band:
            band.append(cid)
    if band:
        return ResolutionDecision(ResolutionAction.ADJUDICATE, candidate_ids=tuple(band))
    return ResolutionDecision(ResolutionAction.CREATE_NEW)


def _type_compatible(candidate_type: str, mention_type: str) -> bool:
    """Types match, or either side is the generic/unknown catch-all."""
    generic = {"", "entity", "thing", "unknown"}
    a, b = candidate_type.casefold(), mention_type.casefold()
    return a == b or a in generic or b in generic


_ADJUDICATE_SYSTEM = (
    "You resolve entity mentions to a canonical entity. Given a mention and a "
    "list of candidate entities, decide which candidate refers to the SAME "
    "real-world entity as the mention. If none does, return null. "
    'Respond with a JSON object: {"match": "<candidate_id>"} or {"match": null}.'
)


class EntityResolver:
    """Resolves entity mentions to canonical ``entity_id``s, creating as needed."""

    def __init__(
        self,
        store: FactStore,
        embedder: EmbeddingProvider,
        *,
        llm: LLMClient | None = None,
        thresholds: ResolutionThresholds | None = None,
        candidate_k: int = 5,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm
        self._thresholds = thresholds or ResolutionThresholds()
        self._k = candidate_k

    def resolve(self, tenant_id: str, name: str, entity_type: str) -> str:
        """Return the canonical ``entity_id`` for a mention, resolving or creating it."""
        embedding = self._embedder.embed(name)

        vector_candidates = [
            (e, s)
            for e, s in self._store.find_entities(tenant_id, embedding=embedding, limit=self._k)
            if _type_compatible(e.type, entity_type)
        ]
        lexical_extra = tuple(
            e.entity_id
            for e, _ in self._store.find_entities(tenant_id, name=name, limit=self._k)
            if _type_compatible(e.type, entity_type)
        )

        decision = decide_resolution(
            vector_candidates, thresholds=self._thresholds, extra_candidate_ids=lexical_extra
        )

        if decision.action is ResolutionAction.MERGE and decision.entity_id:
            self._record_alias(tenant_id, decision.entity_id, name)
            return decision.entity_id

        if decision.action is ResolutionAction.ADJUDICATE and self._llm is not None:
            chosen = self._adjudicate(tenant_id, name, entity_type, decision.candidate_ids)
            if chosen:
                self._record_alias(tenant_id, chosen, name)
                return chosen

        return self._create(tenant_id, name, entity_type, embedding)

    def _create(self, tenant_id: str, name: str, entity_type: str, embedding: list[float]) -> str:
        entity = Entity.make(
            tenant_id=tenant_id,
            canonical_name=name,
            type=entity_type,
            embedding=tuple(embedding),
        )
        self._store.upsert_entities(tenant_id, [entity])
        return entity.entity_id

    def _record_alias(self, tenant_id: str, entity_id: str, mention: str) -> None:
        existing = self._store.get_entity(tenant_id, entity_id)
        if existing is not None and mention.casefold() != existing.canonical_name.casefold():
            self._store.add_aliases(tenant_id, entity_id, [mention])

    def _adjudicate(
        self, tenant_id: str, name: str, entity_type: str, candidate_ids: tuple[str, ...]
    ) -> str | None:
        if self._llm is None:
            return None
        candidates = [self._store.get_entity(tenant_id, cid) for cid in candidate_ids]
        listed = [c for c in candidates if c is not None]
        if not listed:
            return None
        catalog = "\n".join(f"- id={c.entity_id} name={c.canonical_name!r} type={c.type}" for c in listed)
        user = (
            f"Mention: {name!r} (type: {entity_type})\n\nCandidates:\n{catalog}\n\n"
            "Which candidate id is the same entity as the mention? Return JSON."
        )
        try:
            raw = self._llm.complete(
                [LLMMessage("system", _ADJUDICATE_SYSTEM), LLMMessage("user", user)],
                temperature=0.0,
                max_tokens=100,
                json_object=True,
            )
            match = json.loads(raw).get("match")
        except (json.JSONDecodeError, KeyError, AttributeError, ValueError) as exc:
            logger.warning("Entity adjudication failed for %r: %s", name, exc)
            return None
        valid = {c.entity_id for c in listed}
        return match if match in valid else None


def resolve_predicate(registry: PredicateRegistry, raw: str) -> str:
    """Canonical predicate key for a raw extracted predicate phrase."""
    return registry.normalize(raw).key
