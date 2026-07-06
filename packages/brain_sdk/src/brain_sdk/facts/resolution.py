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
import math
from dataclasses import dataclass
from enum import StrEnum

from brain_sdk.embedding.protocol import EmbeddingProvider
from brain_sdk.facts.models import Entity
from brain_sdk.facts.predicates import PredicateRegistry, PredicateSpec
from brain_sdk.facts.protocol import FactStore
from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


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
        # Per-resolver memoization keyed on the normalized mention. A document
        # ingest builds one resolver and mentions the same entity many times;
        # without this each mention re-embeds and can re-run LLM adjudication —
        # unbounded cost for zero benefit (the answer is stable within a run).
        # Mirrors PredicateResolver._cache.
        self._cache: dict[tuple[str, str], str] = {}

    def resolve(self, tenant_id: str, name: str, entity_type: str) -> str:
        """Return the canonical ``entity_id`` for a mention, resolving or creating it."""
        cache_key = (name.casefold(), entity_type.casefold())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        entity_id = self._resolve_uncached(tenant_id, name, entity_type)
        self._cache[cache_key] = entity_id
        return entity_id

    def _resolve_uncached(self, tenant_id: str, name: str, entity_type: str) -> str:
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
        except Exception as exc:  # noqa: BLE001 - provider-specific network/timeout/rate-limit
            # An LLM *outage* (timeout, rate limit, connection error) is
            # operationally distinct from a malformed-but-received response:
            # surface it at ERROR so it isn't mistaken for a benign no-match.
            logger.error("Entity adjudication LLM call failed for %r: %s", name, exc)
            return None
        try:
            match = json.loads(raw).get("match")
        except (json.JSONDecodeError, KeyError, AttributeError, ValueError) as exc:
            logger.warning("Entity adjudication returned malformed response for %r: %s", name, exc)
            return None
        valid = {c.entity_id for c in listed}
        return match if match in valid else None


def resolve_predicate(registry: PredicateRegistry, raw: str) -> str:
    """Canonical predicate key for a raw extracted predicate phrase (string-only)."""
    return registry.normalize(raw).key


class PredicateResolver:
    """Maps raw predicate phrases to canonical keys by *meaning*, not a dictionary.

    Instead of hand-maintaining alias lists for every phrasing ("CEO", "chief
    exec", "runs"…), we embed the small canonical vocabulary once and match an
    unknown phrase to the nearest canonical predicate by cosine similarity. Only
    when nothing is close enough do we mint a new open-domain predicate. This is
    the predicate-side analogue of :class:`EntityResolver`.

    A cheap exact/alias check runs first so known forms never cost an embedding
    call; results are cached per phrase for the lifetime of the resolver.
    """

    def __init__(
        self,
        embedder: EmbeddingProvider,
        registry: PredicateRegistry | None = None,
        *,
        threshold: float = 0.60,
    ) -> None:
        self._embedder = embedder
        self._registry = registry or PredicateRegistry()
        self._threshold = threshold
        self._indexed_specs: list[PredicateSpec] = []
        self._vectors: list[list[float]] | None = None
        self._cache: dict[str, PredicateSpec] = {}

    def _ensure_index(self) -> None:
        if self._vectors is not None:
            return
        self._indexed_specs = list(self._registry.specs())
        labels = [s.label for s in self._indexed_specs]
        self._vectors = self._embedder.embed_batch(labels) if labels else []

    def resolve(self, raw: str) -> PredicateSpec:
        cached = self._cache.get(raw)
        if cached is not None:
            return cached
        spec = self._resolve_uncached(raw)
        self._cache[raw] = spec
        return spec

    def _resolve_uncached(self, raw: str) -> PredicateSpec:
        exact = self._registry.lookup(raw)
        if exact is not None:
            return exact
        self._ensure_index()
        if self._vectors:
            try:
                query_vec = self._embedder.embed(raw)
            except Exception as exc:  # noqa: BLE001 - embedding failure → open-domain fallback
                logger.warning("Predicate embedding failed for %r: %s", raw, exc)
                return self._registry.normalize(raw)
            best_idx, best_score = -1, 0.0
            for idx, vec in enumerate(self._vectors):
                score = _cosine(query_vec, vec)
                if score > best_score:
                    best_idx, best_score = idx, score
            if best_idx >= 0 and best_score >= self._threshold:
                return self._indexed_specs[best_idx]
        # Nothing close enough → keep it as a new open-domain (additive) predicate.
        return self._registry.normalize(raw)

    def key(self, raw: str) -> str:
        """Canonical key for a raw predicate (satisfies ``PredicateNormalizer``)."""
        return self.resolve(raw).key
