"""Tests for text chunking."""

import pytest
from brain_sdk.chunking.chunker import (
    SectionedChunk,
    chunk_text,
    create_sectioned_chunks,
    create_sentence_based_overlapping_chunks,
    text_to_tokens,
    tokens_to_text,
)


class TestTokenization:
    def test_empty_text_returns_empty(self) -> None:
        assert text_to_tokens("") == []

    def test_round_trip(self) -> None:
        text = "Hello, world! This is a test."
        tokens = text_to_tokens(text)
        assert len(tokens) > 0
        assert tokens_to_text(tokens) == text

    def test_empty_tokens_returns_empty(self) -> None:
        assert tokens_to_text([]) == ""


class TestChunking:
    def test_empty_text(self) -> None:
        assert create_sentence_based_overlapping_chunks("", 100, 20) == []

    def test_invalid_overlap_raises(self) -> None:
        with pytest.raises(ValueError, match="must be greater"):
            create_sentence_based_overlapping_chunks("Some text.", 10, 10)

    def test_single_sentence(self) -> None:
        text = "This is a single sentence."
        chunks = create_sentence_based_overlapping_chunks(text, 100, 10)
        assert len(chunks) == 1
        assert "single sentence" in chunks[0]

    def test_multiple_sentences_fit_in_one_chunk(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        chunks = create_sentence_based_overlapping_chunks(text, 1000, 10)
        assert len(chunks) == 1

    def test_multiple_chunks_created(self) -> None:
        sentences = [f"Sentence number {i} has some content." for i in range(50)]
        text = " ".join(sentences)
        chunks = create_sentence_based_overlapping_chunks(text, 50, 10)
        assert len(chunks) > 1

    def test_chunks_respect_token_limit(self) -> None:
        sentences = [f"Sentence {i} with enough words to count." for i in range(100)]
        text = " ".join(sentences)
        chunk_size = 100
        chunks = create_sentence_based_overlapping_chunks(text, chunk_size, 20)

        for chunk in chunks:
            tokens = text_to_tokens(chunk)
            # Allow slight overshoot for long single sentences
            assert len(tokens) <= chunk_size * 1.5

    def test_convenience_wrapper(self) -> None:
        text = "First. Second. Third. Fourth. Fifth."
        chunks = chunk_text(text, desired_chunk_size=1000)
        assert len(chunks) >= 1


class TestSectionedChunks:
    def test_empty_text_returns_empty(self) -> None:
        assert create_sectioned_chunks("", 100, 20) == []

    def test_no_headings_yields_none_section(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        chunks = create_sectioned_chunks(text, 1000, 10)
        assert len(chunks) == 1
        assert isinstance(chunks[0], SectionedChunk)
        assert chunks[0].section is None
        assert "First sentence" in chunks[0].text

    def test_no_headings_matches_plain_chunk_text(self) -> None:
        """With no headings, chunk text must match the plain chunker exactly."""
        sentences = [f"Sentence number {i} has some content." for i in range(40)]
        text = " ".join(sentences)
        plain = create_sentence_based_overlapping_chunks(text, 50, 10)
        sectioned = create_sectioned_chunks(text, 50, 10)
        assert [c.text for c in sectioned] == plain
        assert all(c.section is None for c in sectioned)

    def test_heading_labels_its_chunks(self) -> None:
        text = "# Introduction\nThis is the intro. It has content."
        chunks = create_sectioned_chunks(text, 1000, 10)
        assert all(c.section == "Introduction" for c in chunks)
        # The heading text stays in the body so retrieval still sees it.
        assert any("Introduction" in c.text for c in chunks)

    def test_nested_headings_build_path(self) -> None:
        text = (
            "# Chapter One\nIntro prose here.\n"
            "## Section A\nDetails about A follow here.\n"
            "## Section B\nDetails about B follow here."
        )
        chunks = create_sectioned_chunks(text, 1000, 10)
        sections = {c.section for c in chunks}
        assert "Chapter One" in sections
        assert "Chapter One › Section A" in sections
        assert "Chapter One › Section B" in sections

    def test_deeper_then_shallower_heading_resets_path(self) -> None:
        text = (
            "# A\nAlpha text here.\n"
            "## B\nBravo text here.\n"
            "# C\nCharlie text here."
        )
        chunks = create_sectioned_chunks(text, 1000, 10)
        by_body = {c.section for c in chunks if "Charlie" in c.text}
        # The second H1 must not nest under the earlier H2.
        assert by_body == {"C"}

    def test_text_before_first_heading_has_none_section(self) -> None:
        text = "Preamble prose with no heading yet.\n# Later\nBody under later."
        chunks = create_sectioned_chunks(text, 1000, 10)
        assert any(c.section is None and "Preamble" in c.text for c in chunks)
        assert any(c.section == "Later" for c in chunks)


class TestLongSentence:
    def test_sentence_exceeding_chunk_size(self) -> None:
        """A single sentence longer than chunk_size is emitted as its own chunk.

        The sentence splitter only breaks on a period followed by a capitalised
        word, so the flanking short sentences must start with capitals for the
        long middle sentence to be segmented out. The chunker intentionally does
        not hard-split within a sentence (see test_chunks_respect_token_limit),
        so the oversized sentence occupies one chunk that overshoots chunk_size.
        """
        long_sentence = "The " + "quick brown fox jumps over the lazy dog " * 15 + "again."
        text = f"Short sentence. {long_sentence} Another short one."
        chunk_size = 50
        chunks = create_sentence_based_overlapping_chunks(text, chunk_size, 10)
        # The oversized sentence is split out from its short neighbours.
        assert len(chunks) >= 2
        # ...and it lands in a chunk that exceeds chunk_size on its own.
        assert any(len(text_to_tokens(c)) > chunk_size for c in chunks)
