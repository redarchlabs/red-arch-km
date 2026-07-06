"""Structure-aware extraction — deterministic claims from tables & key-value blocks.

Markdown tables, frontmatter, and definition lists are dense with exactly the
facts an LLM prose pass tends to skim (a directory's rows, a spec's key/value
pairs). Parsing them directly is cheap, deterministic, and high-precision: no
tokens, no hallucination, and every claim carries the source row as provenance.

Output is a list of :class:`ClaimCandidate` — the same currency the LLM extractor
produces — so the ingest pipeline resolves and reconciles both the same way. A
repeat of an LLM-extracted claim simply corroborates it downstream (the fact
store dedups on a stable key), so running both passes is additive, not
duplicative.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from brain_sdk.facts.extraction import ClaimCandidate, EntityMention
from brain_sdk.facts.models import ObjectType
from brain_sdk.facts.predicates import slug_predicate

if TYPE_CHECKING:
    from brain_sdk.facts.doc_profiles import DocumentProfile

# Confidence for a claim read straight out of explicit structure. Higher than the
# LLM default (0.8): a table cell is not an inference.
_STRUCTURED_CONFIDENCE = 0.9

# Common column headers / keys mapped to canonical predicates. Anything not here
# falls back to the slugified header as an open-domain predicate.
_HEADER_PREDICATES: dict[str, str] = {
    "title": "holds_title",
    "role": "holds_title",
    "position": "holds_title",
    "job title": "holds_title",
    "jobtitle": "holds_title",
    "reports to": "reports_to",
    "reportsto": "reports_to",
    "manager": "reports_to",
    "reporting to": "reports_to",
}

# Cells that mean "no value" — skip rather than store an empty/placeholder claim.
_EMPTY_CELLS = frozenset({"", "-", "—", "n/a", "na", "none", "tbd", "null"})

_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _is_separator(line: str) -> bool:
    """A markdown table header/body separator row (``|---|:--:|``)."""
    return "-" in line and bool(_SEPARATOR_RE.match(line))


def _cells(line: str) -> list[str]:
    """Split a markdown table row into trimmed cell values."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _is_empty(cell: str) -> bool:
    return cell.strip().casefold() in _EMPTY_CELLS


def _predicate_for(header: str) -> str:
    key = header.strip().casefold()
    return _HEADER_PREDICATES.get(key) or slug_predicate(header) or "has_attribute"


def extract_structured(
    text: str, profile: DocumentProfile | None = None
) -> list[ClaimCandidate]:
    """Deterministically pull claims from markdown tables in ``text``.

    Each data row's first non-empty cell is the subject entity; every other
    column yields a literal-valued claim ``subject -<header predicate>-> cell``.
    Non-tabular text yields ``[]``. Never raises on malformed structure.
    """
    subject_type = _subject_type(profile)
    candidates: list[ClaimCandidate] = []
    for header, rows in _iter_tables(text.splitlines()):
        candidates.extend(_claims_from_table(header, rows, subject_type))
    return candidates


def _subject_type(profile: DocumentProfile | None) -> str:
    """Best-guess entity type for a table's row-subject from the profile."""
    if profile is not None and profile.entity_types:
        return profile.entity_types[0]
    return "OTHER"


def _iter_tables(lines: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    """Find ``(header_cells, [row_cells, ...])`` for each markdown table.

    A table is a header line, a separator line, then one or more body lines —
    all pipe-delimited. Tables are separated by any non-table line.
    """
    tables: list[tuple[list[str], list[list[str]]]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # A header must be a pipe row immediately followed by a separator row.
        if "|" in line and i + 1 < n and _is_separator(lines[i + 1]):
            header = _cells(line)
            rows: list[list[str]] = []
            j = i + 2
            while j < n and "|" in lines[j] and not _is_separator(lines[j]) and lines[j].strip():
                rows.append(_cells(lines[j]))
                j += 1
            tables.append((header, rows))
            i = j
        else:
            i += 1
    return tables


def _claims_from_table(
    header: list[str], rows: list[list[str]], subject_type: str
) -> list[ClaimCandidate]:
    if len(header) < 2:
        return []  # need a subject column + at least one attribute column
    claims: list[ClaimCandidate] = []
    for row in rows:
        if not row or _is_empty(row[0]):
            continue
        subject = EntityMention(name=row[0].strip(), type=subject_type)
        row_span = " | ".join(c for c in row if c.strip())
        for col_idx in range(1, min(len(header), len(row))):
            head = header[col_idx]
            cell = row[col_idx]
            if not head.strip() or _is_empty(cell):
                continue
            claims.append(
                ClaimCandidate(
                    subject=subject,
                    predicate=_predicate_for(head),
                    text_span=row_span,
                    object_value=cell.strip(),
                    object_value_type=ObjectType.TEXT,
                    confidence=_STRUCTURED_CONFIDENCE,
                )
            )
    return claims


__all__ = ["extract_structured"]
