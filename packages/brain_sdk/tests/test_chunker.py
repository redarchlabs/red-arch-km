"""Tests for text chunking."""

import pytest
from brain_sdk.chunking.chunker import (
    chunk_text,
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
