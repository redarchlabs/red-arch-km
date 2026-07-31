"""Compress text into one short, natural spoken reply via a small LLM.

Used by the workflow ``summarize`` action so a voice surface (e.g. a robot) speaks
a concise, precise line instead of reading a full RAG answer with citation markers.
Kept deliberately tiny and side-effect-free (given a client) so it is easy to test.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from api.services.speech_chunks import SentenceChunker

logger = logging.getLogger(__name__)

# Called with each token as it arrives; may be sync or async.
DeltaSink = Callable[[str], Awaitable[None] | None]
# Called with each completed clause and its 0-based position; may be sync or async. Lets a voice
# surface start speaking sentence one while the model is still writing sentence three, so
# time-to-first-sound stops scaling with the answer's length. The index is what tells the
# consumer whether to interrupt (0) or queue behind what is already being said (>0).
ChunkSink = Callable[[str, int], Awaitable[None] | None]

# Default persona/format: one spoken sentence, factual, no markup or citations.
# The speaker is a character (e.g. a robot), NOT a search interface, so it must never
# break character by referring to its knowledge base, documents, files, sources, or
# uploading — when it lacks the information it simply says it isn't familiar with the topic.
_DEFAULT_INSTRUCTION = (
    "You turn source text into a short, natural reply spoken out loud by a friendly "
    "assistant. Be precise and factual: use ONLY the information in the provided text and "
    "never invent details. Prefer the SPECIFICS the text gives you — names, numbers, "
    "classes, values — over general statements about them: say \"the Magellan carries 10 to "
    "12\" rather than \"crew sizes vary by ship\". When the question covers several items, "
    "name each one with its own detail instead of describing them collectively; a few short "
    "sentences are fine. Only generalise when the text truly lacks the specifics. "
    "Reply in the SAME LANGUAGE the question is asked in, even when the source text is in "
    "another language — a question in Spanish gets a Spanish answer, translating the facts "
    "you found rather than switching the listener to English. "
    "No markdown, no citation markers like [1], no bullet lists, no "
    "preamble such as 'Sure' or 'The answer is'. Speak as yourself, in the first person. "
    "NEVER mention or allude to a knowledge base, documents, files, records, sources, "
    "search results, or uploading — the listener must never be reminded that your answer "
    "comes from stored content. If the provided text does not contain the answer, do NOT "
    "say anything about missing documents or suggest uploading anything; instead simply "
    "reply, warmly and in one sentence, that you are not familiar with that topic "
    '(e.g. "I\'m not familiar with jokes" or "I don\'t know about that").'
)


def _reasoning_effort_for(model: str) -> str | None:
    """Cheapest reasoning tier for ``model``, or None if it rejects the parameter.

    Reasoning models (gpt-5 family, o-series) default to medium effort and can spend
    20s+ of hidden reasoning tokens on a one-sentence condensation, so pin the lowest
    tier. The o-series has no "minimal"; gpt-5-chat-* and non-reasoning models reject
    ``reasoning_effort`` outright.
    """
    name = model.lower()
    if name.startswith("gpt-5-chat"):
        return None
    if name.startswith("gpt-5"):
        return "minimal"
    if name.startswith(("o1", "o3", "o4")):
        return "low"
    return None


async def _emit(sink: DeltaSink, delta: str) -> None:
    """Hand one token to ``sink``, tolerating a sync or async callable.

    Publishing is best-effort: a viewer that has gone away (or a Redis blip) must
    never fail the run that is producing the answer.
    """
    try:
        result = sink(delta)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 — a broken sink must not break the answer
        logger.debug("summary delta sink failed", exc_info=True)


async def _emit_chunk(sink: ChunkSink, chunk: str, index: int) -> None:
    """Hand one completed clause to ``sink``. Best-effort for the same reason as
    :func:`_emit`, and more sharply: a robot that cannot be reached must not cost the
    caller the answer text it was going to store and display."""
    try:
        result = sink(chunk, index)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 — an unreachable voice must not break the answer
        logger.warning("summary chunk sink failed for chunk %d", index, exc_info=True)


async def summarize_for_speech(
    client: Any,
    model: str,
    *,
    text: str,
    question: str | None = None,
    max_words: int = 30,
    instruction: str | None = None,
    on_delta: DeltaSink | None = None,
    on_chunk: ChunkSink | None = None,
) -> str:
    """Return a <= ``max_words`` spoken-style condensation of ``text``.

    ``client`` is an ``AsyncOpenAI`` instance (typed ``Any`` to keep this module
    import-light and mockable). ``question`` gives the model context for what to
    keep. Falls back to the raw text if the model returns nothing.

    Pass ``on_delta`` to also receive each token as it is generated — the return
    value is unchanged, so a caller that ignores streaming behaves exactly as
    before. Used by the robot chat to paint the reply while it is still being
    written instead of waiting for the whole run.

    Pass ``on_chunk`` to receive whole CLAUSES as they complete (see
    :mod:`api.services.speech_chunks`). That is the difference between a robot that stands
    silent for the length of the answer and one that starts speaking after the first phrase:
    with a spoken reply running at roughly half the rate the model writes, it never runs dry.
    Both sinks may be used at once — the screen paints tokens while the voice speaks clauses.
    """
    system = (instruction or _DEFAULT_INSTRUCTION) + f" Keep it to at most {max_words} words."
    # Source text FIRST, question LAST — deliberately, and load-bearing for latency.
    # A self-hosted chat server reuses the KV cache for a prompt's unchanged PREFIX
    # (llama.cpp --cache-reuse). Retrieved passages repeat across the turns of a
    # conversation while the question never does, so leading with the question makes
    # every turn a cache miss and re-evaluates the whole context. Measured on ~2.2k
    # prompt tokens of retrieved passages: 9.8s per follow-up with the question first
    # vs 2.4s with it last (the first, cold turn costs the same either way).
    user = text if not question else f"Text to condense into a spoken reply:\n{text}\n\nQuestion: {question}"
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    effort = _reasoning_effort_for(model)
    if effort is not None:
        kwargs["reasoning_effort"] = effort

    if on_delta is None and on_chunk is None:
        response = await client.chat.completions.create(**kwargs)
        spoken = (response.choices[0].message.content or "").strip()
        return spoken or text.strip()

    chunker = SentenceChunker() if on_chunk is not None else None
    stream = await client.chat.completions.create(**kwargs, stream=True)
    pieces: list[str] = []
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(getattr(choices[0], "delta", None), "content", None)
        if not delta:
            continue
        pieces.append(delta)
        if on_delta is not None:
            await _emit(on_delta, delta)
        if chunker is not None and on_chunk is not None:
            for clause in chunker.push(delta):
                await _emit_chunk(on_chunk, clause, chunker.emitted - 1)
    # The closing clause usually has no trailing boundary to trigger a cut, so without the
    # flush the voice would stop one sentence short of the answer it displayed.
    if chunker is not None and on_chunk is not None:
        tail = chunker.flush()
        if tail:
            await _emit_chunk(on_chunk, tail, chunker.emitted - 1)
    spoken = "".join(pieces).strip()
    return spoken or text.strip()
