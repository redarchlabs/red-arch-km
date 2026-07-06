"""Pure reconciliation logic — the core of "truth".

Given a new claim and the store's current state for it, decide what should
happen. This is deliberately a **pure function** (no I/O) so the truth-making
policy is exhaustively unit-testable, independent of Neo4j/Cypher. The store
reads the relevant existing state, calls :func:`reconcile`, and executes the
returned actions.

Policy:

- Same fact seen again (identical dedup key) → **corroborate** (add provenance,
  raise confidence). No new node.
- ``MULTI`` predicate, new object → **create** (additive).
- ``FUNCTIONAL`` predicate with a differing active value:
  - if the new claim is strictly *newer* than every conflicting value →
    **supersede** them (temporal update; old values keep their history).
  - if any conflict cannot be temporally ordered → **contradict**: keep both,
    mark them contradicted, and surface the conflict rather than silently
    picking a winner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from brain_sdk.facts.models import Claim, ClaimStatus
from brain_sdk.facts.predicates import Cardinality


class ReconcileAction(StrEnum):
    CREATE = "create"
    CORROBORATE = "corroborate"
    SUPERSEDE = "supersede"
    CONTRADICT = "contradict"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """The decision for one incoming claim."""

    action: ReconcileAction
    new_status: ClaimStatus = ClaimStatus.ACTIVE
    # dedup keys of existing claims to retire (supersede) or flag (contradict).
    affected_keys: tuple[str, ...] = field(default_factory=tuple)


def _ordering_stamp(claim: Claim) -> str | None:
    """The timestamp used to order two conflicting functional values.

    Prefer world-time (``valid_from``); fall back to ingest-time
    (``recorded_at``). ISO-8601 strings sort chronologically as plain strings.
    """
    return claim.valid_from or claim.recorded_at or None


def _is_strictly_newer(new: Claim, existing: Claim) -> bool:
    """True iff ``new`` can be confidently ordered *after* ``existing``."""
    new_stamp = _ordering_stamp(new)
    old_stamp = _ordering_stamp(existing)
    if new_stamp is None or old_stamp is None:
        return False
    return new_stamp > old_stamp


def reconcile(
    new_claim: Claim,
    *,
    cardinality: Cardinality,
    existing_same: Claim | None,
    active_conflicts: tuple[Claim, ...],
) -> ReconcileResult:
    """Decide how to store ``new_claim``.

    Args:
        new_claim: the incoming claim.
        cardinality: cardinality of ``new_claim.predicate``.
        existing_same: an existing claim with the identical dedup key, if any.
        active_conflicts: active claims with the same subject+predicate but a
            *different* object (only meaningful for functional predicates).
    """
    if existing_same is not None:
        return ReconcileResult(ReconcileAction.CORROBORATE)

    if cardinality is Cardinality.MULTI or not active_conflicts:
        return ReconcileResult(ReconcileAction.CREATE)

    # Functional predicate with conflicting active value(s).
    if all(_is_strictly_newer(new_claim, c) for c in active_conflicts):
        return ReconcileResult(
            ReconcileAction.SUPERSEDE,
            new_status=ClaimStatus.ACTIVE,
            affected_keys=tuple(c.dedup_key for c in active_conflicts),
        )

    # At least one conflict we cannot order → genuine contradiction. Keep both,
    # flag both, surface it. We only mark the ones we could not order past.
    unresolved = tuple(c for c in active_conflicts if not _is_strictly_newer(new_claim, c))
    return ReconcileResult(
        ReconcileAction.CONTRADICT,
        new_status=ClaimStatus.CONTRADICTED,
        affected_keys=tuple(c.dedup_key for c in unresolved),
    )
