"""Document profiles + per-document brief for type-conditioned extraction.

Generic claim extraction under-pulls from structured, high-value documents: an
employee directory is *entirely* ``person -> role -> reports_to``, yet a
one-size-fits-all prompt treats it like prose and misses the rows. A
:class:`DocumentProfile` conditions extraction on the *kind* of document — which
predicates and entity types matter — plus a short **brief** of the document's
central entities and key points, so every chunk is extracted consistently and
focused on what the document is actually about.

Type detection is **hybrid** (per the design decision): a cheap metadata/title
heuristic first, an LLM classification fallback only when the heuristic is
inconclusive. The brief (central entities + key points) is a single LLM first
pass, produced once per document and reused across its chunks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace

from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    """How to condition extraction for one document.

    ``priority_predicates`` / ``entity_types`` / ``guidance`` come from the
    doc-type template; ``central_entities`` / ``key_points`` are filled in by the
    per-document brief (empty when no LLM brief ran).
    """

    doc_type: str = GENERIC
    priority_predicates: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    guidance: str = ""
    central_entities: tuple[str, ...] = ()
    key_points: tuple[str, ...] = ()

    def with_brief(
        self, *, central_entities: tuple[str, ...], key_points: tuple[str, ...]
    ) -> DocumentProfile:
        """Return a copy carrying the per-document brief."""
        return replace(self, central_entities=central_entities, key_points=key_points)


# Doc-type templates. Predicates need not exist in the seed ontology — extraction
# is open-domain — but naming them here steers the extractor toward the shapes
# that make each document class queryable.
PROFILE_REGISTRY: dict[str, DocumentProfile] = {
    GENERIC: DocumentProfile(doc_type=GENERIC),
    "directory": DocumentProfile(
        doc_type="directory",
        priority_predicates=("holds_title", "reports_to", "manages", "works_for"),
        entity_types=("PERSON", "ORG"),
        guidance=(
            "This is a people/role directory or org chart. For EACH person named, extract their "
            "title/role as a holds_title claim (object = the title text, e.g. 'CMO'), who they "
            "report to (reports_to), and which org/team they belong to (works_for). Treat every "
            "row or entry as its own set of claims — do not skip people."
        ),
    ),
    "contract": DocumentProfile(
        doc_type="contract",
        priority_predicates=("party_to", "effective_date", "expires_on", "governed_by", "obligation_of"),
        entity_types=("ORG", "PERSON", "DATE"),
        guidance=(
            "This is a contract/agreement. Extract the parties (party_to), the effective and "
            "expiry dates (effective_date / expires_on), governing law (governed_by), and key "
            "obligations (obligation_of)."
        ),
    ),
    "meeting_notes": DocumentProfile(
        doc_type="meeting_notes",
        priority_predicates=("decided", "action_item", "owned_by", "due_on", "attended"),
        entity_types=("PERSON", "ORG"),
        guidance=(
            "These are meeting notes. Extract decisions (decided), action items (action_item) "
            "with their owner (owned_by) and due date (due_on), and who attended (attended)."
        ),
    ),
    "policy": DocumentProfile(
        doc_type="policy",
        priority_predicates=("requires", "prohibits", "applies_to", "effective_date"),
        entity_types=("ORG", "CONCEPT"),
        guidance=(
            "This is a policy/procedure. Extract what it requires (requires), prohibits "
            "(prohibits), who/what it applies to (applies_to), and when it takes effect "
            "(effective_date)."
        ),
    ),
}

# Metadata keyword -> doc_type. Matched against title + folder path + tags.
_HEURISTIC_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("directory", "directory"),
    ("roster", "directory"),
    ("org chart", "directory"),
    ("org-chart", "directory"),
    ("staff", "directory"),
    ("employee", "directory"),
    ("personnel", "directory"),
    ("contract", "contract"),
    ("agreement", "contract"),
    (" msa", "contract"),
    (" nda", "contract"),
    (" sow", "contract"),
    ("meeting", "meeting_notes"),
    ("minutes", "meeting_notes"),
    ("standup", "meeting_notes"),
    ("stand-up", "meeting_notes"),
    ("1:1", "meeting_notes"),
    ("policy", "policy"),
    ("policies", "policy"),
    ("handbook", "policy"),
    ("guideline", "policy"),
    ("procedure", "policy"),
    (" sop", "policy"),
)

_KNOWN_TYPES = frozenset(PROFILE_REGISTRY)


def classify_by_metadata(
    *, title: str = "", folder_path: str = "", tags: tuple[str, ...] = ()
) -> str | None:
    """Cheap heuristic doc-type from metadata. ``None`` when inconclusive.

    A ``None`` here is the signal to fall back to the LLM classifier (hybrid).
    """
    haystack = " ".join([title, folder_path, *tags]).lower()
    # Pad so leading-space keywords (" msa", " nda") can match at the start too.
    haystack = f" {haystack} "
    for keyword, doc_type in _HEURISTIC_KEYWORDS:
        if keyword in haystack:
            return doc_type
    return None


_BRIEF_SYSTEM = (
    "You triage a document before fact extraction. Given its title and a text sample, "
    "return ONLY a JSON object: "
    '{"doc_type": one of ["directory","contract","meeting_notes","policy","generic"], '
    '"central_entities": [up to 8 names the document is primarily about], '
    '"key_points": [up to 6 short factual statements the document asserts]}. '
    "Choose 'generic' if none of the specific types clearly fit."
)


@dataclass(frozen=True, slots=True)
class _Brief:
    doc_type: str | None
    central_entities: tuple[str, ...]
    key_points: tuple[str, ...]


class DocumentProfiler:
    """Produce a :class:`DocumentProfile` for a document (hybrid type + brief).

    ``llm=None`` disables the brief pass entirely — profiling then relies solely
    on the metadata heuristic and returns the bare doc-type template (used in
    unit tests and low-cost ingest paths).
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        sample_chars: int = 2000,
        max_tokens: int = 400,
    ) -> None:
        self._llm = llm
        self._sample_chars = sample_chars
        self._max_tokens = max_tokens

    def profile(
        self,
        *,
        title: str = "",
        folder_path: str = "",
        tags: tuple[str, ...] = (),
        sample_text: str = "",
    ) -> DocumentProfile:
        heuristic_type = classify_by_metadata(title=title, folder_path=folder_path, tags=tags)

        # Run the LLM brief when we have a client AND either the type is still
        # unknown (need classification) or there is text worth briefing over.
        brief: _Brief | None = None
        if self._llm is not None and sample_text.strip():
            brief = self._brief(title, sample_text)

        doc_type = heuristic_type or (brief.doc_type if brief else None) or GENERIC
        if doc_type not in _KNOWN_TYPES:
            doc_type = GENERIC

        base = PROFILE_REGISTRY[doc_type]
        if brief is None:
            return base
        return base.with_brief(
            central_entities=brief.central_entities, key_points=brief.key_points
        )

    def _brief(self, title: str, sample_text: str) -> _Brief | None:
        assert self._llm is not None
        sample = sample_text[: self._sample_chars]
        user = f"Title: {title or '(untitled)'}\n\nSample:\n{sample}"
        try:
            raw = self._llm.complete(
                [LLMMessage("system", _BRIEF_SYSTEM), LLMMessage("user", user)],
                temperature=0.1,
                max_tokens=self._max_tokens,
                json_object=True,
            )
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Document brief returned unparseable JSON: %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        doc_type = data.get("doc_type")
        return _Brief(
            doc_type=str(doc_type) if isinstance(doc_type, str) else None,
            central_entities=_str_tuple(data.get("central_entities"), limit=8),
            key_points=_str_tuple(data.get("key_points"), limit=6),
        )


def _str_tuple(value: object, *, limit: int) -> tuple[str, ...]:
    """Coerce an LLM list field to a clean tuple of non-empty strings."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


__all__ = [
    "GENERIC",
    "PROFILE_REGISTRY",
    "DocumentProfile",
    "DocumentProfiler",
    "classify_by_metadata",
]
