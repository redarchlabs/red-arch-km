"""Sentence-boundary-aware text chunking with token-based overlap.

Tokenisation defaults to `o200k_base` — the encoding OpenAI uses for
GPT-4o / GPT-5 families — so chunk sizes match what the downstream
summariser (GPT-5-nano) will actually see.
"""

from __future__ import annotations

import logging

import tiktoken
from sentence_splitter import SentenceSplitter

logger = logging.getLogger(__name__)

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
