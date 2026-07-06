"""Core data model for the knowledge/fact engine.

The engine stores **reified claims** rather than flat triplets. A claim is a
subject–predicate–object statement that additionally carries provenance,
bi-temporal validity, a lifecycle status, and a confidence score — none of
which a plain graph edge can hold. Entities are **canonical**: many surface
forms ("IBM", "I.B.M.") resolve to one node.

All model types are frozen + slotted (immutable) per the project style. Ids are
deterministic hashes so re-ingesting the same content converges instead of
duplicating (idempotency).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

_UNIT_SEP = "\x1f"
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Casefold + collapse whitespace — used only for deterministic id derivation.

    Canonicalisation proper is the resolver's job; this just ensures trivially
    different renderings of an already-canonical name map to one id.
    """
    return _WS.sub(" ", text.strip()).casefold()


def now_iso() -> str:
    """UTC timestamp in ISO-8601 — the single clock for `recorded_at`/`extracted_at`."""
    return datetime.now(tz=UTC).isoformat()


def compute_entity_id(tenant_id: str, entity_type: str, canonical_name: str) -> str:
    """Globally-unique, deterministic entity id.

    Embeds ``tenant_id`` so a single global uniqueness constraint on
    ``:Entity(entity_id)`` is safe across tenants, and embeds type+name so the
    same canonical entity re-resolves to the same node on re-ingest.
    """
    raw = _UNIT_SEP.join((tenant_id, _norm(entity_type), _norm(canonical_name)))
    return hashlib.sha256(raw.encode()).hexdigest()


def compute_dedup_key(tenant_id: str, subject_id: str, predicate: str, object_key: str) -> str:
    """Deterministic identity of a specific (subject, predicate, object) claim.

    Two extractions of the same fact collapse to this key (→ corroboration);
    a differing object yields a different key (→ additive or supersession,
    decided by predicate cardinality during reconciliation).
    """
    raw = _UNIT_SEP.join((tenant_id, subject_id, _norm(predicate), _norm(object_key)))
    return hashlib.sha256(raw.encode()).hexdigest()


class ObjectType(StrEnum):
    """Whether a claim's object is another canonical entity or a literal value."""

    ENTITY = "entity"
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"


class ClaimStatus(StrEnum):
    """Lifecycle of a claim within the bi-temporal model."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    RETRACTED = "retracted"


@dataclass(frozen=True, slots=True)
class Entity:
    """A canonical entity. Surface forms collapse here via resolution."""

    entity_id: str
    tenant_id: str
    canonical_name: str
    type: str
    aliases: tuple[str, ...] = ()
    embedding: tuple[float, ...] | None = None

    @classmethod
    def make(
        cls,
        *,
        tenant_id: str,
        canonical_name: str,
        type: str,
        aliases: tuple[str, ...] = (),
        embedding: tuple[float, ...] | None = None,
    ) -> Entity:
        """Construct with a derived, deterministic ``entity_id``."""
        return cls(
            entity_id=compute_entity_id(tenant_id, type, canonical_name),
            tenant_id=tenant_id,
            canonical_name=canonical_name,
            type=type,
            aliases=aliases,
            embedding=embedding,
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    """One source that supports a claim — the per-fact evidence trail."""

    document_key: str
    chunk_id: str
    text_span: str
    extractor_model: str
    extracted_at: str = field(default_factory=now_iso)
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class Claim:
    """A reified subject–predicate–object statement.

    The object is either another entity (``object_type == ENTITY`` → ``object_id``)
    or a literal (``object_value``). ``valid_from``/``valid_to`` are *world* time;
    ``recorded_at`` is *ingest* time (bi-temporal).
    """

    tenant_id: str
    subject_id: str
    predicate: str
    object_type: ObjectType
    object_id: str | None = None
    object_value: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    recorded_at: str = field(default_factory=now_iso)
    status: ClaimStatus = ClaimStatus.ACTIVE
    confidence: float = 1.0
    access_keys: tuple[int, ...] = ()
    tags: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def __post_init__(self) -> None:
        if self.object_type is ObjectType.ENTITY:
            if not self.object_id:
                msg = "entity-valued claim requires object_id"
                raise ValueError(msg)
        elif not self.object_value:
            msg = f"literal claim ({self.object_type}) requires object_value"
            raise ValueError(msg)

    @property
    def object_key(self) -> str:
        """The identity of the object, whichever form it takes."""
        if self.object_type is ObjectType.ENTITY:
            return self.object_id or ""
        return self.object_value or ""

    @property
    def dedup_key(self) -> str:
        return compute_dedup_key(self.tenant_id, self.subject_id, self.predicate, self.object_key)

    @property
    def claim_id(self) -> str:
        # One stored node per distinct (subject, predicate, object). Supersession
        # retires prior nodes rather than mutating one in place, preserving the
        # full audit trail as linked history.
        return self.dedup_key
