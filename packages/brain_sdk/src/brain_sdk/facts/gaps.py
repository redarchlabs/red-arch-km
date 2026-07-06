"""Knowledge-gap loop — turn failed fact queries into re-extraction targets.

The fact store is deliberately sparse (extraction is bounded by the ontology +
salience), so it will never be "complete". The demand-driven answer to "what
should I extract?" is: *the questions that came up empty*. When the agent runs a
fact tool (claim_query / entity_lookup / neighborhood) and gets zero rows, that
is a precise signal that a fact users want is missing — a gap worth recording
and, later, re-extracting for.

This module holds the pure, storage-agnostic core: the gap-detection decision,
the record model, and a :class:`GapLog` protocol (with an in-memory
implementation). brain-api supplies a persistent (Neo4j-backed) log and wires
capture into the agent loop plus review/re-extraction endpoints.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

# Tools that read the structured fact store. A zero-row return from these — not
# from search_passages (which reads raw text) — is what marks a *fact* gap.
FACT_TOOLS: frozenset[str] = frozenset({"claim_query", "entity_lookup", "neighborhood"})

_NON_WORD = re.compile(r"[^a-z0-9]+")


class GapStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class GapAssessment:
    """Pure verdict on whether one agent result represents a fact gap."""

    is_gap: bool
    fact_rows: int
    tools_used: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    """A recorded question the fact store could not answer."""

    gap_id: str
    tenant_id: str
    question: str
    dedup_key: str
    answer: str = ""
    tools_used: tuple[str, ...] = ()
    fact_rows: int = 0
    occurrences: int = 1
    status: GapStatus = GapStatus.OPEN
    created_at: str = ""
    last_seen_at: str = ""
    resolved_at: str | None = None


def dedup_key(question: str) -> str:
    """Stable key so the same recurring question folds into one gap."""
    return _NON_WORD.sub("_", question.strip().casefold()).strip("_")


def assess_gap(evidence: Sequence[Mapping[str, Any]]) -> GapAssessment:
    """Decide whether an agent result is a fact gap, from its evidence trail.

    A gap requires that a fact tool was actually *attempted* (so we don't flag
    thematic questions that only needed passages) and that the fact tools
    together returned zero rows.
    """
    tools_used = tuple(str(e.get("tool", "")) for e in evidence)
    fact_entries = [e for e in evidence if e.get("tool") in FACT_TOOLS]
    fact_rows = sum(len(e.get("result") or []) for e in fact_entries)
    is_gap = bool(fact_entries) and fact_rows == 0
    return GapAssessment(is_gap=is_gap, fact_rows=fact_rows, tools_used=tools_used)


def build_gap(
    *,
    gap_id: str,
    tenant_id: str,
    question: str,
    answer: str,
    assessment: GapAssessment,
    now: str,
) -> KnowledgeGap:
    """Construct a :class:`KnowledgeGap` from an assessment (id + clock injected)."""
    return KnowledgeGap(
        gap_id=gap_id,
        tenant_id=tenant_id,
        question=question.strip(),
        dedup_key=dedup_key(question),
        answer=answer,
        tools_used=assessment.tools_used,
        fact_rows=assessment.fact_rows,
        created_at=now,
        last_seen_at=now,
    )


class GapLog(Protocol):
    """Persistence for knowledge gaps (tenant-scoped)."""

    def record(self, gap: KnowledgeGap) -> KnowledgeGap:
        """Insert a gap, or fold it into an existing open one (dedup_key).

        Returns the stored gap (with an incremented ``occurrences`` when folded).
        """
        ...

    def list_open(self, tenant_id: str, *, limit: int = 100) -> list[KnowledgeGap]: ...

    def set_status(
        self, tenant_id: str, gap_id: str, status: GapStatus, *, resolved_at: str | None = None
    ) -> bool:
        """Update a gap's status. Returns ``False`` if not found."""
        ...


class InMemoryGapLog:
    """Non-persistent :class:`GapLog` — the default and the test double."""

    def __init__(self) -> None:
        self._by_id: dict[tuple[str, str], KnowledgeGap] = {}

    def record(self, gap: KnowledgeGap) -> KnowledgeGap:
        # Fold into an existing OPEN gap with the same dedup_key.
        for key, existing in self._by_id.items():
            if (
                key[0] == gap.tenant_id
                and existing.dedup_key == gap.dedup_key
                and existing.status is GapStatus.OPEN
            ):
                folded = replace(
                    existing,
                    occurrences=existing.occurrences + 1,
                    last_seen_at=gap.last_seen_at or existing.last_seen_at,
                    answer=gap.answer or existing.answer,
                )
                self._by_id[key] = folded
                return folded
        self._by_id[(gap.tenant_id, gap.gap_id)] = gap
        return gap

    def list_open(self, tenant_id: str, *, limit: int = 100) -> list[KnowledgeGap]:
        open_gaps = [
            g
            for (tid, _), g in self._by_id.items()
            if tid == tenant_id and g.status is GapStatus.OPEN
        ]
        open_gaps.sort(key=lambda g: (g.occurrences, g.last_seen_at), reverse=True)
        return open_gaps[:limit]

    def set_status(
        self, tenant_id: str, gap_id: str, status: GapStatus, *, resolved_at: str | None = None
    ) -> bool:
        gap = self._by_id.get((tenant_id, gap_id))
        if gap is None:
            return False
        self._by_id[(tenant_id, gap_id)] = replace(gap, status=status, resolved_at=resolved_at)
        return True


__all__ = [
    "FACT_TOOLS",
    "GapAssessment",
    "GapLog",
    "GapStatus",
    "InMemoryGapLog",
    "KnowledgeGap",
    "assess_gap",
    "build_gap",
    "dedup_key",
]
