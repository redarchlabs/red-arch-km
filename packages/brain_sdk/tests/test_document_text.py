"""Lexical search + whole-document reads (brain_sdk.document_text).

The store is faked: these pin what the reader does with what the index returns —
verification, ranking, snippets, stitching, paging and fail-closed scope. The
index's own matching is exercised against a real Qdrant in brain_api's
integration suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from brain_sdk.document_text import (
    MAX_TERMS,
    DocumentTextReader,
    ReadScope,
    overlap_length,
    parse_terms,
    snippet,
    stitch,
)
from brain_sdk.vector_store.protocol import SearchResult


def _chunk(key: str, order: int, text: str, *, title: str = "Doc", section: str | None = None) -> SearchResult:
    return SearchResult(
        id=f"{key}-{order}",
        score=0.0,
        payload={
            "document_key": key,
            "document_title": title,
            "chunk_order": order,
            "section": section,
            "text": text,
        },
    )


class FakeStore:
    """Returns every chunk containing the terms' words (any case), like the index."""

    def __init__(self, chunks: list[SearchResult]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def text_search(self, tenant_id: str, terms: list[str], **kwargs: Any) -> list[SearchResult]:
        self.calls.append(("text_search", {"terms": terms, **kwargs}))

        def has(text: str, term: str) -> bool:
            return all(word in text.lower() for word in term.lower().split())

        match = all if kwargs.get("match_all", True) else any
        hits = [c for c in self.chunks if match(has(c.payload["text"], t) for t in terms)]
        return hits[: kwargs.get("limit", 400)]

    def document_chunk_window(
        self, tenant_id: str, document_key: str, *, start: int = 0, limit: int = 40, **kwargs: Any
    ) -> tuple[list[SearchResult], int]:
        self.calls.append(("window", {"start": start, "limit": limit, **kwargs}))
        doc = [c for c in self.chunks if c.payload["document_key"] == document_key]
        window = [c for c in doc if start <= c.payload["chunk_order"] < start + limit]
        return sorted(window, key=lambda c: c.payload["chunk_order"]), len(doc)

    def has_chunks_from(self, tenant_id: str, document_key: str, *, start: int, **kwargs: Any) -> bool:
        self.calls.append(("probe", {"start": start, **kwargs}))
        return any(c.payload["document_key"] == document_key and c.payload["chunk_order"] >= start for c in self.chunks)


def _reader(chunks: list[SearchResult]) -> tuple[DocumentTextReader, FakeStore]:
    store = FakeStore(chunks)
    return DocumentTextReader(store), store  # type: ignore[arg-type]


OPEN = ReadScope()


class TestParseTerms:
    def test_words_split_on_whitespace_and_lowercase(self) -> None:
        assert parse_terms("Atlas  Asset", "all") == ["atlas", "asset"]

    def test_punctuated_identifiers_stay_whole(self) -> None:
        assert parse_terms("PMP-2041-X", "all") == ["pmp-2041-x"]

    def test_quoted_runs_stay_together(self) -> None:
        assert parse_terms('"relief valve" leak', "any") == ["relief valve", "leak"]

    def test_phrase_is_one_needle(self) -> None:
        assert parse_terms("  The Quick   Brown ", "phrase") == ["the quick brown"]

    def test_duplicates_dropped_and_terms_capped(self) -> None:
        many = " ".join(f"w{i}" for i in range(MAX_TERMS + 5))
        assert len(parse_terms(many, "all")) == MAX_TERMS
        assert parse_terms("a A a", "all") == ["a"]

    def test_blank_query_has_no_terms(self) -> None:
        assert parse_terms("   ", "all") == []
        assert parse_terms('""', "phrase") == []

    def test_terms_without_letters_or_digits_are_dropped(self) -> None:
        """The index matches words, so ``--`` or ``§`` could only ever match nothing
        (or, unindexed, everything)."""
        assert parse_terms("-- § pump", "all") == ["pump"]
        assert parse_terms("--", "phrase") == []


class TestGrep:
    def test_finds_an_exact_identifier_dense_search_would_miss(self) -> None:
        reader, _ = _reader(
            [
                _chunk("pumps", 0, "An overview of the plant and its history.", title="Pumps"),
                _chunk("pumps", 4, "ATLAS unit, asset tag PMP-2041-X, rated 40 kW.", title="Pumps"),
                _chunk("other", 0, "Nothing relevant here.", title="Other"),
            ]
        )
        out = reader.grep("t", "PMP-2041-X", scope=OPEN)
        assert out["documents_matched"] == 1
        doc = out["documents"][0]
        assert doc["document_key"] == "pumps"
        assert doc["snippets"][0]["chunk"] == 4
        # Quoted in the original casing, not the lowercase it was matched in.
        assert "ATLAS unit, asset tag PMP-2041-X" in doc["snippets"][0]["text"]

    def test_all_mode_needs_every_term_in_one_chunk(self) -> None:
        reader, _ = _reader(
            [
                _chunk("split", 0, "alpha only"),
                _chunk("split", 1, "beta only"),
                _chunk("together", 0, "alpha and beta"),
            ]
        )
        out = reader.grep("t", "alpha beta", scope=OPEN)
        assert [d["document_key"] for d in out["documents"]] == ["together"]

    def test_any_mode_ranks_documents_with_more_distinct_terms_first(self) -> None:
        reader, _ = _reader(
            [
                _chunk("one", 0, "alpha alpha alpha alpha"),
                _chunk("both", 0, "alpha beta"),
            ]
        )
        out = reader.grep("t", "alpha beta", scope=OPEN, mode="any")
        assert [d["document_key"] for d in out["documents"]] == ["both", "one"]

    def test_phrase_mode_rejects_words_out_of_order(self) -> None:
        """The index answers "these words occur"; the phrase needs them adjacent."""
        reader, _ = _reader(
            [
                _chunk("scrambled", 0, "valve on the relief line"),
                _chunk("exact", 0, "the Relief   Valve was opened"),
            ]
        )
        out = reader.grep("t", "relief valve", scope=OPEN, mode="phrase")
        assert [d["document_key"] for d in out["documents"]] == ["exact"]

    def test_title_hit_ranks_first_then_occurrences(self) -> None:
        reader, _ = _reader(
            [
                _chunk("many", 0, "pump pump pump pump", title="Notes"),
                _chunk("titled", 0, "the pump", title="Pump Manual"),
                _chunk("few", 0, "pump pump", title="Log"),
            ]
        )
        out = reader.grep("t", "pump", scope=OPEN)
        assert [d["document_key"] for d in out["documents"]] == ["titled", "many", "few"]
        assert out["documents"][1]["occurrences"] == 4

    def test_snippets_are_in_reading_order_and_capped(self) -> None:
        reader, _ = _reader([_chunk("d", order, f"chunk {order} mentions pump") for order in (7, 2, 5, 1)])
        doc = reader.grep("t", "pump", scope=OPEN, snippets_per_document=2)["documents"][0]
        assert doc["matching_chunks"] == 4
        assert [s["chunk"] for s in doc["snippets"]] == [1, 2]

    def test_max_documents_caps_the_list_not_the_count(self) -> None:
        reader, _ = _reader([_chunk(f"d{i}", 0, "pump") for i in range(5)])
        out = reader.grep("t", "pump", scope=OPEN, max_documents=2)
        assert len(out["documents"]) == 2
        assert out["documents_matched"] == 5

    def test_truncation_means_more_matched_than_were_examined(self) -> None:
        reader, _ = _reader([_chunk(f"d{i}", 0, "pump") for i in range(5)])
        assert reader.grep("t", "pump", scope=OPEN, candidates=4)["truncated"] is True
        # Exactly the cap matched: everything was examined.
        assert reader.grep("t", "pump", scope=OPEN, candidates=5)["truncated"] is False
        assert reader.grep("t", "pump", scope=OPEN, candidates=4)["documents_matched"] == 4

    def test_overlapping_needles_count_once(self) -> None:
        reader, _ = _reader([_chunk("d", 0, "the relief valve hums")])
        doc = reader.grep("t", 'relief "relief valve"', scope=OPEN, mode="any")["documents"][0]
        assert doc["occurrences"] == 1

    def test_unknown_mode_falls_back_to_all(self) -> None:
        reader, _ = _reader([_chunk("d", 0, "alpha")])
        assert reader.grep("t", "alpha", scope=OPEN, mode="regex")["mode"] == "all"

    def test_scope_filters_are_passed_to_the_store(self) -> None:
        reader, store = _reader([])
        reader.grep("t", "pump", scope=ReadScope(access_keys=[0, 7], required_tags=["x"], any_tags=["folder:a"]))
        _, kwargs = store.calls[0]
        assert kwargs["access_keys"] == [0, 7]
        assert kwargs["required_tags"] == ["x"]
        assert kwargs["any_tags"] == ["folder:a"]

    @pytest.mark.parametrize(
        "scope",
        [ReadScope(access_keys=[]), ReadScope(any_tags=[])],
        ids=["no-masks", "no-folders"],
    )
    def test_an_empty_scope_reads_nothing_and_never_queries(self, scope: ReadScope) -> None:
        """The store treats [] as "no filter"; the reader must refuse first."""
        reader, store = _reader([_chunk("d", 0, "pump")])
        assert reader.grep("t", "pump", scope=scope)["documents"] == []
        assert store.calls == []


class TestRead:
    def test_reads_the_whole_document_in_order(self) -> None:
        reader, _ = _reader([_chunk("d", i, f"Sentence {i}.", title="Manual") for i in (2, 0, 1)])
        out = reader.read("t", "d", scope=OPEN)
        assert out is not None
        assert out["title"] == "Manual"
        assert out["text"] == "Sentence 0. Sentence 1. Sentence 2."
        assert out["next_chunk"] is None
        assert (out["start_chunk"], out["end_chunk"], out["total_chunks"]) == (0, 3, 3)

    def test_pages_by_characters_and_says_where_to_continue(self) -> None:
        reader, _ = _reader([_chunk("d", i, "x" * 100) for i in range(10)])
        first = reader.read("t", "d", scope=OPEN, max_chars=250)
        assert first is not None
        assert (first["end_chunk"], first["next_chunk"]) == (2, 2)
        second = reader.read("t", "d", scope=OPEN, start_chunk=first["next_chunk"], max_chars=250)
        assert second is not None
        assert second["start_chunk"] == 2 and second["next_chunk"] == 4

    def test_a_chunk_larger_than_the_budget_still_makes_progress(self) -> None:
        reader, _ = _reader([_chunk("d", 0, "y" * 500), _chunk("d", 1, "z")])
        out = reader.read("t", "d", scope=OPEN, max_chars=10)
        assert out is not None and out["next_chunk"] == 1

    def test_reads_past_one_store_window(self) -> None:
        reader, store = _reader([_chunk("d", i, f"s{i}") for i in range(95)])
        out = reader.read("t", "d", scope=OPEN, max_chars=10_000)
        assert out is not None and out["next_chunk"] is None
        assert out["text"].endswith("s94")
        # Three full windows, then one empty one confirmed as the end by a probe.
        assert len([c for c in store.calls if c[0] == "window"]) == 4
        assert [c[1]["start"] for c in store.calls if c[0] == "probe"] == [160]

    def test_starting_past_the_end_returns_empty_text(self) -> None:
        reader, _ = _reader([_chunk("d", 0, "only")])
        out = reader.read("t", "d", scope=OPEN, start_chunk=5)
        assert out is not None and out["text"] == "" and out["next_chunk"] is None

    def test_a_gap_in_the_numbering_is_read_past_not_looped_on(self) -> None:
        """Orders {0..9, 60..64}: a count-bounded cursor stopped at 10 and kept
        telling the caller to continue from 10, which returned nothing, forever."""
        reader, _ = _reader([_chunk("d", i, f"s{i}") for i in [*range(10), *range(60, 65)]])
        out = reader.read("t", "d", scope=OPEN)
        assert out is not None
        assert out["text"].endswith("s64") and "s9 s60" in out["text"]
        assert out["next_chunk"] is None

    def test_a_tail_beyond_the_chunk_count_is_still_read(self) -> None:
        """Orders 0..44 with five missing: the count is 40, but chunks 40-44 exist."""
        orders = [i for i in range(45) if i not in {3, 7, 11, 19, 23}]
        reader, _ = _reader([_chunk("d", i, f"s{i}") for i in orders])
        out = reader.read("t", "d", scope=OPEN)
        assert out is not None and out["text"].endswith("s44") and out["next_chunk"] is None

    def test_duplicate_chunks_are_read_once(self) -> None:
        reader, _ = _reader([_chunk("d", i, f"Sentence {i}.") for i in (0, 1, 1, 2, 2)])
        out = reader.read("t", "d", scope=OPEN)
        assert out is not None and out["text"] == "Sentence 0. Sentence 1. Sentence 2."
        assert out["next_chunk"] is None

    def test_next_chunk_is_only_offered_for_a_chunk_left_unread(self) -> None:
        reader, _ = _reader([_chunk("d", i, "x" * 100) for i in (0, 1, 1, 2)])
        first = reader.read("t", "d", scope=OPEN, max_chars=150)
        assert first is not None and first["next_chunk"] == 1
        rest = reader.read("t", "d", scope=OPEN, start_chunk=1, max_chars=10_000)
        assert rest is not None and rest["next_chunk"] is None

    def test_an_oversized_chunk_is_cut_to_the_budget(self) -> None:
        reader, _ = _reader([_chunk("d", 0, "y" * 5000), _chunk("d", 1, "z")])
        out = reader.read("t", "d", scope=OPEN, max_chars=1000)
        assert out is not None
        assert len(out["text"]) < 1100 and "omitted" in out["text"]
        assert out["next_chunk"] == 1

    def test_missing_and_unreadable_documents_look_the_same(self) -> None:
        reader, _ = _reader([_chunk("d", 0, "secret")])
        assert reader.read("t", "nope", scope=OPEN) is None
        assert reader.read("t", "d", scope=ReadScope(access_keys=[])) is None
        assert reader.read("t", "d", scope=ReadScope(any_tags=[])) is None


class TestStitch:
    def test_removes_the_sentence_overlap_ingest_added(self) -> None:
        chunks = [
            {"text": "First sentence here. The overlapping sentence repeats."},
            {"text": "The overlapping sentence repeats. Then new material."},
        ]
        assert stitch(chunks) == "First sentence here. The overlapping sentence repeats. Then new material."

    def test_short_coincidental_overlap_is_kept(self) -> None:
        assert overlap_length("ends with the", "the start") == 0

    def test_overlap_must_end_on_a_word_boundary(self) -> None:
        assert overlap_length("a long enough tail text", "tail textbook continues") == 0

    def test_overlap_must_start_on_a_word_boundary(self) -> None:
        assert overlap_length("ends in xsection four.", "section four. Next.") == 0

    def test_an_overlap_longer_than_ingest_makes_is_real_text(self) -> None:
        passage = " ".join(f"word{i}" for i in range(80))  # ~550 chars, nothing repeats
        assert overlap_length(f"Intro. {passage}", f"{passage} Outro.") == 0

    def test_a_chunk_that_carries_its_own_heading_is_not_given_a_second(self) -> None:
        chunks = [
            {"text": "## Payment Payment is a flat rate.", "section": "Trips › Payment"},
            {"text": "## Prep Book a bus.", "section": "Trips › Prep"},
        ]
        assert stitch(chunks) == "## Payment Payment is a flat rate.\n\n## Prep Book a bus."

    def test_no_overlap_is_looked_for_across_a_section_change(self) -> None:
        """Ingest never overlaps across sections, so a matching sentence there is real."""
        chunks = [
            {"text": "Closing note: all rights reserved.", "section": "Terms"},
            {"text": "Closing note: all rights reserved. Annex begins.", "section": None},
        ]
        assert stitch(chunks).count("all rights reserved") == 2

    def test_a_section_change_becomes_a_heading(self) -> None:
        chunks = [
            {"text": "Intro.", "section": "Guide"},
            {"text": "More intro.", "section": "Guide"},
            {"text": "Steps.", "section": "Guide › Setup"},
        ]
        assert stitch(chunks) == "## Guide\n\nIntro. More intro.\n\n## Guide › Setup\n\nSteps."


class TestSnippet:
    def test_cuts_at_word_boundaries_with_ellipses(self) -> None:
        text = "one two three four five TARGET six seven eight nine ten"
        start = text.index("TARGET")
        out = snippet(text, start, len("TARGET"), radius=10)
        assert out.startswith("…") and out.endswith("…")
        assert "TARGET" in out
        assert not out.strip("…").startswith(" ")

    def test_no_ellipsis_when_the_whole_text_fits(self) -> None:
        assert snippet("short TARGET text", 6, 6) == "short TARGET text"
