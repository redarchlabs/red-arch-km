"""Lexical search and whole-document reads over the indexed chunk store.

Dense retrieval answers "which passages are *about* this". It cannot answer
"where does this exact name appear", and it hands the model a few isolated
passages rather than the document they came from. An agent working over a folder
of files does both all the time: it greps for a term, then opens the file and
reads around the hit. This module gives KM2's agents the same two moves.

* :meth:`DocumentTextReader.grep` — find every document containing the words or
  phrase, ranked by how often they occur, with a snippet around each hit.
* :meth:`DocumentTextReader.read` — read one document in reading order, a window
  at a time, stitched back together from its chunks.

Both work over the chunk store rather than the original files because that is
the one place every document's text lives, whatever its format (an uploaded PDF
has no text column anywhere else), and because every chunk already carries the
access keys and folder tags that decide who may read it. The same visibility
filter as ranked search is applied to every read, so neither tool can reach a
passage that search would hide.

**Scope is fail-closed.** ``access_keys=None`` means unrestricted; an explicit
empty list means nothing is readable, and both tools then answer as if the corpus
were empty without querying anything. The store treats ``[]`` as "no filter", so
that check lives here, before any store call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from brain_sdk.vector_store.protocol import SearchResult, VectorStore

MatchMode = Literal["all", "any", "phrase"]
MATCH_MODES: tuple[str, ...] = ("all", "any", "phrase")

# Terms beyond this are dropped: a query this long is a sentence, which is what
# ranked search is for.
MAX_TERMS = 12
# Chunks pulled from the lexical index per search. Past this the result says it
# was truncated and the caller should narrow the query.
DEFAULT_CANDIDATES = 400
# Characters of context either side of a hit in a snippet.
SNIPPET_RADIUS = 160
# Reads are paged by characters, not chunks, so a caller can budget its context.
DEFAULT_READ_CHARS = 16_000
MAX_READ_CHARS = 60_000
# Chunk orders fetched per store round-trip while assembling a read, and a bound
# on round-trips so a pathologically numbered document cannot spin a read.
_READ_WINDOW = 40
_MAX_WINDOWS = 500
# Ingest overlaps consecutive chunks of a section by whole sentences, at most ~20
# tokens. An overlap shorter than the minimum is more likely a coincidence ("the"
# ending one chunk and starting the next) than a repeated sentence; one longer
# than the maximum cannot be ingest's, so it is real text and is kept.
_MIN_OVERLAP_CHARS = 12
_MAX_OVERLAP_CHARS = 300

_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"\w")


@dataclass(frozen=True, slots=True)
class ReadScope:
    """Who is reading: the filters every chunk read applies. Never model-supplied.

    ``access_keys`` — permission masks (MatchAny); ``None`` = unrestricted, ``[]`` =
    nothing readable. ``required_tags`` — every one must be present. ``any_tags`` —
    folder tags, ORed; ``None`` = every folder, ``[]`` = no folder readable.
    """

    access_keys: list[int] | None = None
    required_tags: list[str] | None = None
    any_tags: list[str] | None = None

    @property
    def is_empty(self) -> bool:
        """An explicit empty scope: nothing may be read."""
        return (self.access_keys is not None and not self.access_keys) or (
            self.any_tags is not None and not self.any_tags
        )

    def filters(self) -> dict[str, Any]:
        return {
            "access_keys": self.access_keys,
            "required_tags": self.required_tags or None,
            "any_tags": self.any_tags,
        }


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace — the form matching compares in."""
    return _WHITESPACE.sub(" ", text).strip().lower()


def parse_terms(query: str, mode: str) -> list[str]:
    """The normalized needles a query asks for.

    ``phrase`` is one needle, the whole query. ``all``/``any`` split on whitespace,
    so ``PMP-2041-X`` stays one term (and must appear as written), while
    ``Atlas asset`` is two. Quoted runs are kept together in those modes too:
    ``"relief valve" leak`` is the phrase plus one word.

    A term with no letters or digits (``--``, ``§``) is dropped: the index matches
    words, so it has nothing to match it with.
    """
    if mode == "phrase":
        needle = normalize(query.strip().strip('"'))
        return [needle] if _WORD.search(needle) else []
    terms: list[str] = []
    for quoted, bare in re.findall(r'"([^"]+)"|(\S+)', query):
        term = normalize(quoted or bare)
        if _WORD.search(term) and term not in terms:
            terms.append(term)
    return terms[:MAX_TERMS]


def _occurrences(haystack: str, needle: str) -> list[int]:
    """Start offsets of every non-overlapping occurrence of ``needle``."""
    out: list[int] = []
    start = haystack.find(needle)
    while start != -1:
        out.append(start)
        start = haystack.find(needle, start + len(needle))
    return out


def snippet(text: str, start: int, length: int, radius: int = SNIPPET_RADIUS) -> str:
    """``text`` around ``[start, start+length)``, cut at word boundaries, with
    ellipses where it was cut."""
    lo = max(0, start - radius)
    hi = min(len(text), start + length + radius)
    if lo > 0:
        space = text.find(" ", lo, start)
        lo = space + 1 if space != -1 else lo
    if hi < len(text):
        space = text.rfind(" ", start + length, hi)
        hi = space if space != -1 else hi
    return f"{'…' if lo > 0 else ''}{text[lo:hi].strip()}{'…' if hi < len(text) else ''}"


def overlap_length(previous: str, following: str) -> int:
    """Characters at the start of ``following`` that repeat the end of ``previous``.

    Ingest overlaps neighbouring chunks by whole sentences, so a read that simply
    concatenated chunks would say every overlapping sentence twice. Only an
    overlap that starts and ends on word boundaries, and is the length of ingest's
    overlap (see ``_MIN_OVERLAP_CHARS``), counts.

    A sentence the document genuinely repeats across a chunk boundary is
    indistinguishable from ingest's overlap and is shown once. That needs the same
    sentence twice in a row, exactly where a chunk happened to end.
    """
    limit = min(len(previous), len(following), _MAX_OVERLAP_CHARS)
    for k in range(limit, _MIN_OVERLAP_CHARS - 1, -1):
        ends_on_word = k == len(following) or following[k] == " "
        starts_on_word = k == len(previous) or previous[-k - 1] == " "
        if ends_on_word and starts_on_word and previous.endswith(following[:k]):
            return k
    return 0


def stitch(chunks: list[dict[str, Any]], *, previous_section: str | None = None) -> str:
    """Join consecutive chunk payloads back into readable text.

    A change of ``section`` (the heading path ingest recorded) starts a new
    paragraph under that heading, which is what makes a long read navigable; the
    overlap ingest added between neighbouring chunks is removed. Ingest never
    overlaps across sections, so no overlap is looked for at a section change.
    """
    parts: list[str] = []
    prev_text = ""
    section = previous_section
    for chunk in chunks:
        text = str(chunk.get("text") or "").strip()
        chunk_section = chunk.get("section") or None
        if chunk_section != section:
            # A Markdown section's first chunk opens with its own heading line;
            # only a chunk that does not (a read starting mid-section) needs the
            # heading path to say where it is.
            if chunk_section and not text.startswith("#"):
                parts.append(f"\n\n## {chunk_section}\n\n")
            elif parts:
                parts.append("\n\n")
            section = chunk_section
            prev_text = ""
        if not text:
            continue
        if prev_text:
            cut = overlap_length(prev_text, text)
            text = text[cut:].lstrip()
            if not text:
                continue
            parts.append(" ")
        parts.append(text)
        prev_text = text
    return "".join(parts).strip()


@dataclass(slots=True)
class _DocumentHits:
    document_key: str
    title: str
    title_hit: bool
    terms_seen: set[str]
    occurrences: int
    chunks: list[tuple[int, str | None, str, list[tuple[int, int]]]]  # (order, section, text, spans)

    def rank(self) -> tuple[int, int, int]:
        return (int(self.title_hit), len(self.terms_seen), self.occurrences)


def _chunk_spans(text: str, needles: list[str]) -> tuple[list[tuple[int, int]], set[str]]:
    """``(start, length)`` of every needle hit in ``text`` (lowercase, whitespace
    collapsed — the form :func:`normalize` puts needles in).

    Overlapping hits count once ("relief" inside "relief valve" is one occurrence).
    """
    spans: list[tuple[int, int]] = []
    seen: set[str] = set()
    for needle in needles:
        hits = _occurrences(text, needle)
        if hits:
            seen.add(needle)
            spans.extend((h, len(needle)) for h in hits)
    merged: list[tuple[int, int]] = []
    for start, length in sorted(spans, key=lambda span: (span[0], -span[1])):
        if merged and start < merged[-1][0] + merged[-1][1]:
            continue
        merged.append((start, length))
    return merged, seen


def _satisfies(seen: set[str], needles: list[str], mode: str) -> bool:
    if mode == "any":
        return bool(seen)
    return len(seen) == len(needles)


class DocumentTextReader:
    """Lexical search + paged whole-document reads over a :class:`VectorStore`."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    # -- grep ------------------------------------------------------------

    def grep(
        self,
        tenant_id: str,
        query: str,
        *,
        scope: ReadScope,
        mode: str = "all",
        max_documents: int = 10,
        snippets_per_document: int = 3,
        candidates: int = DEFAULT_CANDIDATES,
    ) -> dict[str, Any]:
        """Documents containing the query's words (or phrase), best match first.

        Ranking: a hit in the title first, then how many distinct terms the
        document contains (it matters for ``any``), then how many times they occur.
        Each document carries up to ``snippets_per_document`` snippets in reading
        order, each with the ``chunk`` number :meth:`read` can start from.

        In ``all`` mode a document qualifies only if a single chunk holds every
        term; terms spread across distant sections do not make a match. That is
        deliberate — it is the "these words appear together" a grep implies.
        """
        mode = mode if mode in MATCH_MODES else "all"
        needles = parse_terms(query, mode)
        empty: dict[str, Any] = {
            "query": query,
            "mode": mode,
            "documents": [],
            "documents_matched": 0,
            "truncated": False,
        }
        if not needles or scope.is_empty:
            return empty

        # One more than the cap, so "exactly the cap matched" is not misread as
        # "there were more".
        points = self._store.text_search(
            tenant_id,
            needles,
            match_all=mode != "any",
            limit=candidates + 1,
            **scope.filters(),
        )
        truncated = len(points) > candidates
        by_doc = self._collect(points[:candidates], needles, mode)
        ranked = sorted(by_doc.values(), key=lambda d: d.rank(), reverse=True)
        return {
            **empty,
            "documents": [self._shape(doc, snippets_per_document) for doc in ranked[:max_documents]],
            "documents_matched": len(ranked),
            # The candidate cap was hit, so some matching chunks were never looked
            # at — and which ones were is arbitrary (the index returns them in
            # storage order). The counts are a floor and the ranking is partial.
            "truncated": truncated,
        }

    @staticmethod
    def _collect(points: list[SearchResult], needles: list[str], mode: str) -> dict[str, _DocumentHits]:
        by_doc: dict[str, _DocumentHits] = {}
        for point in points:
            payload = point.payload
            key = str(payload.get("document_key") or "")
            if not key:
                continue
            # Match in lowercase, but quote the original casing: an agent cites
            # snippets verbatim. Lowercasing almost never changes a string's length;
            # when it does, offsets no longer line up and the lowercase text is shown.
            display = _WHITESPACE.sub(" ", str(payload.get("text") or "")).strip()
            lowered = display.lower()
            spans, seen = _chunk_spans(lowered, needles)
            text = display if len(display) == len(lowered) else lowered
            # The index matched words; the needle may need them adjacent, in order,
            # or with punctuation ("PMP-2041-X"). Only verified chunks count.
            if not _satisfies(seen, needles, mode):
                continue
            title = str(payload.get("document_title") or "Untitled")
            doc = by_doc.get(key)
            if doc is None:
                title_norm = normalize(title)
                doc = _DocumentHits(
                    document_key=key,
                    title=title,
                    title_hit=any(n in title_norm for n in needles),
                    terms_seen=set(),
                    occurrences=0,
                    chunks=[],
                )
                by_doc[key] = doc
            doc.terms_seen |= seen
            doc.occurrences += len(spans)
            doc.chunks.append((int(payload.get("chunk_order") or 0), payload.get("section"), text, spans))
        return by_doc

    @staticmethod
    def _shape(doc: _DocumentHits, snippets_per_document: int) -> dict[str, Any]:
        doc.chunks.sort(key=lambda c: c[0])
        return {
            "document_key": doc.document_key,
            "title": doc.title,
            "occurrences": doc.occurrences,
            "matching_chunks": len(doc.chunks),
            "snippets": [
                {
                    "chunk": order,
                    "section": section,
                    "text": snippet(text, spans[0][0], spans[0][1]),
                }
                for order, section, text, spans in doc.chunks[:snippets_per_document]
            ],
        }

    # -- read ------------------------------------------------------------

    def read(
        self,
        tenant_id: str,
        document_key: str,
        *,
        scope: ReadScope,
        start_chunk: int = 0,
        max_chars: int = DEFAULT_READ_CHARS,
    ) -> dict[str, Any] | None:
        """One document from ``start_chunk``, up to about ``max_chars`` characters.

        Returns ``None`` when the document does not exist or the scope cannot read
        it — the two are deliberately indistinguishable. Otherwise the stitched
        text plus where it stopped: ``next_chunk`` is the ``start_chunk`` that
        continues the read, or ``None`` at the end of the document.
        """
        if scope.is_empty or not document_key:
            return None
        start_chunk = max(0, start_chunk)
        max_chars = max(1, min(max_chars, MAX_READ_CHARS))
        filters = scope.filters()

        taken: list[dict[str, Any]] = []
        seen: set[int] = set()
        size = 0
        cursor = start_chunk
        total = 0
        next_chunk: int | None = None
        for _ in range(_MAX_WINDOWS):
            window, total = self._store.document_chunk_window(
                tenant_id, document_key, start=cursor, limit=_READ_WINDOW, **filters
            )
            if total == 0:
                return None
            cursor += _READ_WINDOW
            if not window:
                # An empty window is the end of the document, unless a later chunk
                # exists — then it was a gap in the numbering, and reading goes on.
                if self._store.has_chunks_from(tenant_id, document_key, start=cursor, **filters):
                    continue
                break
            for result in window:
                order = int(result.payload.get("chunk_order") or 0)
                if order in seen:  # a duplicate left by an older, appending re-ingest
                    continue
                text = str(result.payload.get("text") or "")
                if taken and size + len(text) > max_chars:
                    next_chunk = order
                    break
                payload = result.payload
                if len(text) > max_chars:
                    # One chunk larger than the whole budget (a run of text with no
                    # sentence breaks). Take its head so the read still progresses.
                    payload = {**payload, "text": f"{text[:max_chars]} …[rest of this chunk omitted]"}
                seen.add(order)
                taken.append(payload)
                size += len(payload["text"])
            if next_chunk is not None:
                break
        else:
            # Round-trip bound reached with chunks possibly still ahead: hand back
            # a place to continue rather than claim the document ended.
            if self._store.has_chunks_from(tenant_id, document_key, start=cursor, **filters):
                next_chunk = cursor
        return self._read_result(document_key, taken, start_chunk, total, next_chunk)

    @staticmethod
    def _read_result(
        document_key: str,
        taken: list[dict[str, Any]],
        start_chunk: int,
        total: int,
        next_chunk: int | None,
    ) -> dict[str, Any]:
        end = int(taken[-1].get("chunk_order") or 0) + 1 if taken else start_chunk
        title = str(taken[0].get("document_title") or "Untitled") if taken else ""
        return {
            "document_key": document_key,
            "title": title,
            "total_chunks": total,
            "start_chunk": start_chunk,
            "end_chunk": end,
            # Set only when a chunk was actually left unread, so following it can
            # never loop on an empty page.
            "next_chunk": next_chunk,
            "text": stitch(taken),
        }
