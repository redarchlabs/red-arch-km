"""Cut a stream of LLM deltas into speakable clauses.

The summarize action receives the answer a token at a time; a voice wants whole clauses. This
turns one into the other so a robot can start speaking the first sentence while the model is
still writing the third — which takes time-to-first-sound off the answer's LENGTH. A detailed
answer then *sounds* as fast as a terse one, which is what makes a generous word budget safe.

Two rules shape the design:

* **The first chunk leaves early.** All the latency the listener notices is the wait for chunk
  one, so the opening cut takes the earliest respectable boundary — a comma will do — and
  ignores the ``min_chars`` floor that later chunks obey. Later chunks prefer sentence ends,
  because a synthesizer needs the whole clause to place its prosody.
* **No word is ever lost, duplicated, or reordered.** For any input split at any delta
  boundaries, ``" ".join(chunks).split() == text.split()``. This decides only WHERE to cut,
  never what is said.

The robot service has its own copy of this idea (``app/speech_chunker.py``) for the path where
it drives its own brain. This one serves the opposite direction — KM2's workflow engine as the
brain, pushing speech out — so the two are deliberately separate: they share a shape, not a
process, and coupling them would mean shipping one to speak for the other.
"""

from __future__ import annotations

import re

# Sentence-final punctuation. The lookahead is the load-bearing part: a boundary only counts
# when the NEXT character has already arrived, because the buffer's end is not a boundary — it
# is the middle of whatever the model is still writing. Allowing ``$`` here cut "10–12 crew"
# into "10–" and "12 crew" the moment the dash was the last character received, and the robot
# said "ten dash" out loud. Full-width CJK stops need no following space to be unambiguous, so
# they may cut at the tip; ASCII ones must be followed by space or a closing quote/bracket.
_SENTENCE_END = re.compile(r"[.!?…](?=[\s\"'”’)\]])|[。！？]")
# Softer boundaries, only ever used for the FIRST chunk: a comma-length opener is worth
# speaking immediately, but chopping every later clause this finely would sound clipped.
# Deliberately NO dashes — an en dash in "10–12" or "6–8" is a range, not a clause break.
_SOFT_BREAK = re.compile(r"[,;:](?=\s)")

# Don't emit a later chunk until it is at least this long. Prevents "Yes." "Right." dribbling
# out as separate utterances, each with its own engine start-up cost and prosody reset.
_MIN_CHARS = 60
# …but never hold more than this waiting for punctuation. A model that writes a long clause
# with no stop (a list, a table read aloud) must not silence the robot until it finishes.
_MAX_CHARS = 240
# The first chunk only needs to be a plausible phrase. Below this, waiting is better than
# speaking a fragment like "The" or "It is".
_MIN_FIRST_CHARS = 12


class SentenceChunker:
    """Accumulate deltas; emit clauses as they complete.

    Stateful and single-use per answer::

        chunker = SentenceChunker()
        for delta in stream:
            for chunk in chunker.push(delta):
                speak(chunk)
        tail = chunker.flush()
        if tail:
            speak(tail)
    """

    def __init__(self, *, min_chars: int = _MIN_CHARS, max_chars: int = _MAX_CHARS) -> None:
        self._buf = ""
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._emitted = 0

    @property
    def emitted(self) -> int:
        """How many chunks have been handed out — the caller's cue for whether to append."""
        return self._emitted

    def push(self, delta: str) -> list[str]:
        """Add a delta and return whatever became speakable (possibly nothing)."""
        if not delta:
            return []
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            chunk, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
            if chunk:
                out.append(chunk)
                self._emitted += 1
        return out

    def flush(self) -> str | None:
        """The unspoken remainder, or None. Always call it: the last sentence of an answer
        usually has no trailing whitespace to trigger a boundary, and dropping it would cut
        the robot off before its final word."""
        tail, self._buf = self._buf.strip(), ""
        if not tail:
            return None
        self._emitted += 1
        return tail

    def _find_cut(self) -> int | None:
        """Index to cut the buffer at, or None to keep waiting."""
        first = self._emitted == 0
        floor = _MIN_FIRST_CHARS if first else self._min_chars

        # A completed sentence is always the best cut, provided there is enough of it.
        for match in _SENTENCE_END.finditer(self._buf):
            if match.end() >= floor:
                return match.end()

        # The opening chunk may settle for a comma — the listener is waiting on it.
        if first:
            for match in _SOFT_BREAK.finditer(self._buf):
                if match.end() >= floor:
                    return match.end()

        # No punctuation in sight and the buffer has grown too long to keep holding. Cut at the
        # last word boundary rather than mid-word, so nothing is ever split in half.
        if len(self._buf) >= self._max_chars:
            space = self._buf.rfind(" ", 0, self._max_chars)
            if space > floor:
                return space
        return None
