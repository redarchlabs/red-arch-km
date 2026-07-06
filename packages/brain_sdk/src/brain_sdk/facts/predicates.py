"""Predicate ontology and normalisation.

Free-form extraction yields ``reports_to`` / ``is managed by`` / ``works under``
as three different relationships. We resolve every raw predicate to a canonical
key. Each predicate declares a **cardinality** that drives reconciliation:

- ``FUNCTIONAL`` — one current value per subject (``headquartered_in``); a new
  differing value *supersedes* the old.
- ``MULTI`` — many values allowed (``authored``); new values are additive and
  repeats *corroborate*.

Unknown predicates are accepted (open-domain) and default to ``MULTI`` so we
never wrongly discard a value; they can be promoted to functional later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slug_predicate(raw: str) -> str:
    """Normalise a raw predicate phrase to a snake_case key."""
    return _NON_WORD.sub("_", raw.strip().casefold()).strip("_")


class Cardinality(StrEnum):
    FUNCTIONAL = "functional"
    MULTI = "multi"


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    """A canonical predicate."""

    key: str
    label: str
    cardinality: Cardinality
    inverse: str | None = None
    aliases: tuple[str, ...] = ()


# Seed vocabulary. Intentionally small — the registry accepts unknown predicates
# (defaulting to MULTI); this seed just pins cardinality/aliases for the common,
# high-value ones so supersession behaves correctly out of the box.
DEFAULT_PREDICATES: tuple[PredicateSpec, ...] = (
    PredicateSpec(
        "headquartered_in",
        "headquartered in",
        Cardinality.FUNCTIONAL,
        aliases=("hq in", "based in", "located in", "head office in"),
    ),
    PredicateSpec("founded_in", "founded in", Cardinality.FUNCTIONAL, aliases=("established in", "founded")),
    PredicateSpec("date_of_birth", "date of birth", Cardinality.FUNCTIONAL, aliases=("born on", "born", "dob")),
    PredicateSpec(
        "reports_to",
        "reports to",
        Cardinality.FUNCTIONAL,
        inverse="manages",
        aliases=("is managed by", "works under", "manager is"),
    ),
    PredicateSpec("manages", "manages", Cardinality.MULTI, inverse="reports_to", aliases=("manager of", "leads")),
    PredicateSpec(
        "ceo_of",
        "CEO of",
        Cardinality.FUNCTIONAL,
        aliases=("chief executive of", "ceo", "ceo of", "chief executive officer", "chief executive officer of"),
    ),
    PredicateSpec("acquired", "acquired", Cardinality.MULTI, aliases=("bought", "purchased", "took over")),
    PredicateSpec("authored", "authored", Cardinality.MULTI, aliases=("wrote", "author of", "written by")),
    PredicateSpec(
        "works_for", "works for", Cardinality.MULTI, inverse="employs", aliases=("employed by", "employee of")
    ),
    PredicateSpec("employs", "employs", Cardinality.MULTI, inverse="works_for"),
    PredicateSpec(
        "part_of", "part of", Cardinality.FUNCTIONAL, inverse="has_part", aliases=("belongs to", "subsidiary of")
    ),
    PredicateSpec("mentions", "mentions", Cardinality.MULTI, aliases=("references", "refers to")),
    PredicateSpec("related_to", "related to", Cardinality.MULTI, aliases=("associated with", "connected to")),
)


class PredicateRegistry:
    """Maps raw predicate phrases to canonical specs."""

    def __init__(self, specs: tuple[PredicateSpec, ...] = DEFAULT_PREDICATES) -> None:
        self._by_key: dict[str, PredicateSpec] = {}
        self._alias_index: dict[str, str] = {}
        for spec in specs:
            self._register(spec)

    def _register(self, spec: PredicateSpec) -> None:
        self._by_key[spec.key] = spec
        self._alias_index[spec.key] = spec.key
        for alias in spec.aliases:
            self._alias_index[slug_predicate(alias)] = spec.key

    def keys(self) -> tuple[str, ...]:
        """Canonical keys, for prompting the extractor with the known vocabulary."""
        return tuple(self._by_key)

    def normalize(self, raw: str) -> PredicateSpec:
        """Resolve a raw predicate to a canonical spec.

        Exact key or known alias → that spec. Otherwise the slug is accepted as a
        new open-domain predicate defaulting to ``MULTI``.
        """
        slug = slug_predicate(raw)
        canonical = self._alias_index.get(slug)
        if canonical is not None:
            return self._by_key[canonical]
        # Open-domain fallback: keep the value, default to additive semantics.
        return PredicateSpec(slug, raw.strip(), Cardinality.MULTI)

    def cardinality(self, key: str) -> Cardinality:
        spec = self._by_key.get(key)
        return spec.cardinality if spec is not None else Cardinality.MULTI
