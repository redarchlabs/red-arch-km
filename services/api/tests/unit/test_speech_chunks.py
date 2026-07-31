"""Cutting a delta stream into speakable clauses.

The property that matters most is not where the cuts land but that the words survive them: the
chunker decides WHERE the robot pauses, never WHAT it says. Everything else is latency shaping.
"""

from __future__ import annotations

import pytest
from api.services.speech_chunks import SentenceChunker

pytestmark = pytest.mark.unit

ANSWER = (
    "The Meridian has 10-12 crew and is an Heavy Class Carrier. The Kestrel has 9-11 crew. "
    "The Halcyon has 6-7 crew and serves as the fleet's only battleship. The Solaris is a "
    "shuttlecraft with 5-6 crew."
)


def _stream(text: str, size: int) -> list[str]:
    """The same text arriving in fixed-size deltas — a stand-in for token boundaries."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def _run(text: str, size: int) -> list[str]:
    chunker = SentenceChunker()
    out: list[str] = []
    for delta in _stream(text, size):
        out.extend(chunker.push(delta))
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


class TestWordsSurvive:
    @pytest.mark.parametrize("size", [1, 2, 3, 5, 13, 60, 500])
    def test_no_word_is_lost_duplicated_or_reordered(self, size: int) -> None:
        """Whatever the delta boundaries — one character at a time or the whole answer at
        once — the spoken words are exactly the written ones, in order."""
        assert " ".join(_run(ANSWER, size)).split() == ANSWER.split()

    @pytest.mark.parametrize("size", [1, 4, 40])
    def test_holds_for_text_with_no_punctuation_at_all(self, size: int) -> None:
        text = "word " * 200
        assert " ".join(_run(text, size)).split() == text.split()

    def test_empty_stream_says_nothing(self) -> None:
        chunker = SentenceChunker()
        assert chunker.push("") == []
        assert chunker.flush() is None


class TestFirstChunkLeavesEarly:
    """Time-to-first-sound is the whole point: it must not scale with the answer's length."""

    def test_opening_clause_is_emitted_before_the_answer_finishes(self) -> None:
        chunker = SentenceChunker()
        first = chunker.push("The Meridian has 10-12 crew, ")
        assert first == ["The Meridian has 10-12 crew,"]
        # …and it happened on a comma, without waiting for the sentence to end.
        assert chunker.emitted == 1

    def test_a_bare_fragment_is_not_worth_speaking(self) -> None:
        assert SentenceChunker().push("The, ") == []

    def test_later_chunks_wait_for_a_sentence_not_a_comma(self) -> None:
        chunker = SentenceChunker()
        chunker.push("Opening clause here, ")  # consumes the early-exit allowance
        assert chunker.push("then a following clause, and another, ") == []

    def test_short_sentences_are_grouped_rather_than_dribbled(self) -> None:
        """One utterance per two-word sentence would mean an engine restart and a prosody
        reset for each — it sounds like a stutter."""
        chunker = SentenceChunker()
        chunker.push("A long enough opening clause to be spoken on its own. ")
        assert chunker.push("Yes. ") == []
        assert chunker.push("No. ") == []


class TestNeverStallsForPunctuation:
    def test_a_long_unpunctuated_run_is_still_spoken(self) -> None:
        """A model reading a table aloud can go a long way without a full stop. Waiting for
        one would leave the robot silent for the whole answer."""
        chunker = SentenceChunker(max_chars=100)
        chunks = chunker.push("Meridian Kestrel Halcyon Corvair Solaris Sierra " * 4)
        assert chunks, "buffer exceeded max_chars and still produced nothing"

    def test_the_forced_cut_lands_between_words(self) -> None:
        chunker = SentenceChunker(max_chars=80)
        text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike"
        chunks = chunker.push(text)
        tail = chunker.flush()
        spoken = chunks + ([tail] if tail else [])
        assert " ".join(spoken).split() == text.split()
        for chunk in spoken:
            assert chunk == chunk.strip()


class TestNonEnglish:
    def test_spanish_and_cjk_sentence_ends_are_boundaries(self) -> None:
        """The robot answers in the language it is asked in, so clause detection cannot be
        English-only or a Spanish answer arrives as one unbroken block."""
        text = "La nave más pequeña es el Solaris. Tiene una tripulación de 5 a 6 miembros. "
        chunks = _run(text, 7)
        assert len(chunks) >= 2
        assert " ".join(chunks).split() == text.split()

    def test_full_width_stop_breaks(self) -> None:
        chunker = SentenceChunker()
        assert chunker.push("这是一个足够长的句子可以单独朗读出来。") == ["这是一个足够长的句子可以单独朗读出来。"]


class TestTheBufferEndIsNotABoundary:
    """Regression: cutting at the end of the received buffer treats the middle of a word as a
    clause break. Caught live — the robot said "The Meridian has ten dash" and then "twelve
    crew", because the en dash of "10–12" happened to be the last character received."""

    def test_a_dash_range_is_never_split(self) -> None:
        chunker = SentenceChunker()
        # Exactly how it arrived: the dash lands at the tip of the buffer, then the rest follows.
        assert chunker.push("The Meridian has 10–") == []
        out = chunker.push("12 crew. The Kestrel has 9–11. ")
        tail = chunker.flush()
        spoken = " ".join(x for x in [*out, tail] if x)
        assert "10–12" in spoken
        assert "10– 12" not in spoken

    def test_punctuation_at_the_tip_waits_for_the_next_character(self) -> None:
        """A trailing '.' may be a sentence end or a decimal point — the next character decides,
        so nothing is emitted until it arrives."""
        chunker = SentenceChunker()
        assert chunker.push("The bridge crew numbers 10.") == []
        assert chunker.push("5 on average. ") != []

    def test_a_full_width_stop_may_cut_at_the_tip(self) -> None:
        """CJK text puts no space after 。, so requiring one would hold a Chinese answer to the
        very end. The character is unambiguous, unlike an ASCII period."""
        chunker = SentenceChunker()
        assert chunker.push("这是一个足够长的句子可以单独朗读出来。") == ["这是一个足够长的句子可以单独朗读出来。"]

    def test_the_words_still_survive_the_stricter_rule(self) -> None:
        text = "Crew is 10–12 on the Meridian, 9–11 on the Kestrel, and 6–8 on the Corvair. That is all."
        for size in (1, 3, 7, 40):
            chunker = SentenceChunker()
            out: list[str] = []
            for i in range(0, len(text), size):
                out.extend(chunker.push(text[i : i + size]))
            tail = chunker.flush()
            if tail:
                out.append(tail)
            assert " ".join(out).split() == text.split(), size
