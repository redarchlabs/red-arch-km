"""Digest layer — GraphRAG-style community summaries over the fact graph.

The direct analog of a curated ``MEMORY.md``: a cheap, always-available
orientation layer the agent can consult first for *global / thematic* questions
("what are the main themes across all documents") that no single fact lookup or
passage retrieval answers well.

Communities are connected components over the entity→entity relationship graph
(pure union-find, unit-tested). Each multi-entity community gets an LLM-written
summary, stored back in the graph as a ``:Community`` node. This layer is fully
derived and regenerable, so it carries no schema risk and is rebuilt on demand.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from brain_sdk.facts.models import Entity
from brain_sdk.facts.protocol import FactStore
from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


def detect_communities(entity_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, str]:
    """Assign each entity to a community via union-find over ``edges``.

    Returns a mapping ``entity_id -> community_representative`` (the smallest
    member id in its connected component, for stability). Singletons map to
    themselves. Pure — no I/O.
    """
    parent: dict[str, str] = {e: e for e in entity_ids}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        # Path compression.
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # Attach the larger id under the smaller so the representative is stable.
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        parent[hi] = lo

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    return {e: find(e) for e in entity_ids}


_SUMMARY_SYSTEM = (
    "You summarise a community of related entities from a knowledge graph. "
    "Given the entities and their relationships, write 1-3 sentences capturing "
    "what this cluster is about and how its members relate. Be factual and concise."
)


class DigestBuilder:
    """Builds and stores community summaries for a tenant's fact graph."""

    def __init__(
        self,
        store: FactStore,
        llm: LLMClient,
        *,
        max_members_summarised: int = 40,
        claims_per_member: int = 8,
        min_community_size: int = 2,
    ) -> None:
        self._store = store
        self._llm = llm
        self._max_members = max_members_summarised
        self._claims_per_member = claims_per_member
        self._min_size = min_community_size

    def build(self, tenant_id: str) -> int:
        """(Re)build community summaries for a tenant. Returns communities written."""
        entities = self._store.iter_entities(tenant_id)
        if not entities:
            return 0
        by_id = {e.entity_id: e for e in entities}
        edges = self._store.entity_relationships(tenant_id)
        assignment = detect_communities(list(by_id), edges)

        groups: dict[str, list[str]] = defaultdict(list)
        for entity_id, rep in assignment.items():
            groups[rep].append(entity_id)

        written = 0
        for rep, members in groups.items():
            if len(members) < self._min_size:
                continue  # singletons form no community
            summary = self._summarise(tenant_id, [by_id[m] for m in members])
            if not summary:
                continue
            self._store.upsert_community(tenant_id, f"{tenant_id}:{rep}", summary, members)
            written += 1
        logger.info("Built %d community summaries for tenant %s", written, tenant_id)
        return written

    def _summarise(self, tenant_id: str, members: list[Entity]) -> str:
        facts: list[str] = []
        for entity in members[: self._max_members]:
            rows = self._store.query_claims(
                tenant_id, subject_id=entity.entity_id, limit=self._claims_per_member
            )
            facts.extend(f"{r.get('subject')} {r.get('predicate')} {r.get('object')}" for r in rows)

        names = ", ".join(e.canonical_name for e in members[: self._max_members])
        relationships = "\n".join(f"- {f}" for f in facts[:80]) or "(no explicit relationships)"
        user = f"Entities: {names}\n\nRelationships:\n{relationships}"
        try:
            return self._llm.complete(
                [LLMMessage("system", _SUMMARY_SYSTEM), LLMMessage("user", user)],
                temperature=0.2,
                max_tokens=200,
            ).strip()
        except Exception as exc:  # noqa: BLE001 - a summary failure skips one community, not the build
            logger.warning("Community summary failed: %s", exc)
            return ""
