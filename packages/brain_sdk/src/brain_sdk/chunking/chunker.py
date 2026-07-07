"""Sentence-boundary-aware text chunking with token-based overlap.

Tokenisation defaults to `o200k_base` — the encoding OpenAI uses for
GPT-4o / GPT-5 families — so chunk sizes match what the downstream
summariser (GPT-5-nano) will actually see.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

import tiktoken
from sentence_splitter import SentenceSplitter

logger = logging.getLogger(__name__)

# Matches a Markdown ATX heading line: 1-6 leading '#', a space, then the title.
# Up to 3 leading spaces are tolerated (CommonMark allows them); 4+ would be a
# code block. Setext (===/---) underlines are intentionally not handled — they
# are rare in the plain-text/markdown sources this pipeline ingests.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SECTION_SEP = " › "

_MAX_ITERATIONS = 10_000_000
# Default encoding for modern OpenAI models (GPT-4o, GPT-5 family).
# Older models like text-embedding-ada-002 use cl100k_base; callers can
# pass that explicitly if mixing models. tiktoken's encoding_for_model
# is the source of truth but falls back here when the model name isn't
# yet registered in tiktoken's table.
DEFAULT_ENCODING = "o200k_base"

_tokenizer_cache: dict[str, tiktoken.Encoding] = {}


def _get_tokenizer(encoding_name: str = DEFAULT_ENCODING) -> tiktoken.Encoding:
    if encoding_name not in _tokenizer_cache:
        _tokenizer_cache[encoding_name] = tiktoken.get_encoding(encoding_name)
    return _tokenizer_cache[encoding_name]


def get_tokenizer_for_model(model: str) -> tiktoken.Encoding:
    """Return the best tokenizer for a given OpenAI model name.

    Falls back to DEFAULT_ENCODING when tiktoken doesn't yet know the
    model (e.g. a newly released gpt-5-nano before the tiktoken registry
    catches up).
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        logger.debug("tiktoken has no mapping for %s; using %s", model, DEFAULT_ENCODING)
        return _get_tokenizer(DEFAULT_ENCODING)


def text_to_tokens(text: str, encoding_name: str = DEFAULT_ENCODING) -> list[int]:
    if not text:
        return []
    return _get_tokenizer(encoding_name).encode(text)


def tokens_to_text(tokens: list[int], encoding_name: str = DEFAULT_ENCODING) -> str:
    if not tokens:
        return ""
    return _get_tokenizer(encoding_name).decode(tokens)


def create_sentence_based_overlapping_chunks(
    full_text: str,
    chunk_size_tokens: int,
    overlap_tokens: int,
    encoding_name: str = DEFAULT_ENCODING,
    language: str = "en",
) -> list[str]:
    """Split text into chunks that respect sentence boundaries with token-based overlap.

    Args:
        full_text: The text to chunk.
        chunk_size_tokens: Maximum tokens per chunk.
        overlap_tokens: Number of overlap tokens between consecutive chunks.
        encoding_name: Tiktoken encoding name. Defaults to o200k_base, which
            matches modern OpenAI models (gpt-4o, gpt-5 family).
        language: Language for sentence splitting.

    Returns:
        List of text chunks.

    Raises:
        ValueError: If chunk_size_tokens <= overlap_tokens.
        RuntimeError: If iteration limit is exceeded (safety guard).
    """
    if not full_text:
        return []
    if chunk_size_tokens <= overlap_tokens:
        msg = "chunk_size_tokens must be greater than overlap_tokens"
        raise ValueError(msg)

    tokenizer = _get_tokenizer(encoding_name)
    splitter = SentenceSplitter(language=language)
    sentences = splitter.split(full_text)
    sentence_token_lengths = [len(tokenizer.encode(s)) for s in sentences]

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_token_count = 0
    i = 0
    iterations = 0

    while i < len(sentences):
        iterations += 1
        if iterations > _MAX_ITERATIONS:
            msg = "Iteration limit exceeded — possible infinite loop"
            raise RuntimeError(msg)

        sentence = sentences[i]
        token_count = sentence_token_lengths[i]

        # Long sentence that exceeds chunk size on its own
        if token_count > chunk_size_tokens:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_token_count = 0
            chunks.append(sentence)
            i += 1
            continue

        if current_token_count + token_count <= chunk_size_tokens:
            current_chunk.append(sentence)
            current_token_count += token_count
            i += 1
        else:
            chunks.append(" ".join(current_chunk))

            # Build overlap from the tail of the current chunk
            overlap_chunk: list[str] = []
            overlap_token_count = 0
            j = len(current_chunk) - 1
            while j >= 0:
                s = current_chunk[j]
                s_len = sentence_token_lengths[i - (len(current_chunk) - j)]
                if overlap_token_count + s_len > overlap_tokens:
                    break
                overlap_chunk.insert(0, s)
                overlap_token_count += s_len
                j -= 1

            current_chunk = overlap_chunk
            current_token_count = overlap_token_count

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def chunk_text(
    text: str,
    desired_chunk_size: int = 500,
    desired_overlap: int = 20,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[str]:
    """Convenience wrapper around create_sentence_based_overlapping_chunks."""
    return create_sentence_based_overlapping_chunks(
        text,
        chunk_size_tokens=desired_chunk_size,
        overlap_tokens=desired_overlap,
        encoding_name=encoding_name,
    )


class SectionedChunk(NamedTuple):
    """A chunk of text plus the document section (heading path) it came from.

    ``section`` is the ``›``-joined path of the enclosing Markdown headings
    (e.g. ``"Chapter 1 › Overview"``), or ``None`` when the chunk falls under
    no heading — e.g. plain prose or OCR'd text with no structural markers.
    Retrieval uses it to label a citation with the specific passage's section.
    """

    text: str
    section: str | None


def _split_by_headings(full_text: str) -> list[tuple[str | None, str]]:
    """Split text into ``(section_path, segment_text)`` blocks at Markdown headings.

    Each heading opens a new segment; the heading's own line is kept in the
    segment body so retrieval/embedding still sees the heading words. The
    section path reflects the heading hierarchy: a deeper heading nests under
    its ancestors, a same-or-shallower heading replaces them. Text before the
    first heading (or a document with no headings at all) yields a single
    segment with ``section=None``.
    """
    lines = full_text.split("\n")
    segments: list[tuple[str | None, str]] = []
    stack: list[tuple[int, str]] = []  # (heading level, title)
    current_lines: list[str] = []
    current_section: str | None = None

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            segments.append((current_section, body))

    for line in lines:
        match = _HEADING_RE.match(line)
        if not match:
            current_lines.append(line)
            continue
        # Close the section that was accumulating before this heading.
        flush()
        level = len(match.group(1))
        title = match.group(2).strip()
        # Pop headings at the same or deeper level so the path reflects nesting.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        current_section = _SECTION_SEP.join(t for _, t in stack)
        current_lines = [line]  # keep the heading text in the segment body

    flush()
    return segments if segments else [(None, full_text.strip())]


def create_sectioned_chunks(
    full_text: str,
    chunk_size_tokens: int,
    overlap_tokens: int,
    encoding_name: str = DEFAULT_ENCODING,
    language: str = "en",
) -> list[SectionedChunk]:
    """Chunk text while tracking the Markdown section each chunk belongs to.

    Text is first split at heading boundaries (so a chunk never straddles two
    sections), then each section is chunked with the same sentence-boundary +
    token-overlap algorithm as :func:`create_sentence_based_overlapping_chunks`.
    Overlap does not cross section boundaries — a reasonable trade since the
    section label is what makes a citation precise.

    Returns an empty list for empty input. For text with no headings the result
    is identical chunk *text* to the plain chunker, each tagged ``section=None``.
    """
    if not full_text:
        return []

    result: list[SectionedChunk] = []
    for section, segment_text in _split_by_headings(full_text):
        for chunk in create_sentence_based_overlapping_chunks(
            segment_text,
            chunk_size_tokens=chunk_size_tokens,
            overlap_tokens=overlap_tokens,
            encoding_name=encoding_name,
            language=language,
        ):
            result.append(SectionedChunk(text=chunk, section=section))
    return result
