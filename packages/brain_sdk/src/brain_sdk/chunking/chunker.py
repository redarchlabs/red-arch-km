"""Sentence-boundary-aware text chunking with token-based overlap."""

from __future__ import annotations

import tiktoken
from sentence_splitter import SentenceSplitter

_MAX_ITERATIONS = 10_000_000
_tokenizer_cache: dict[str, tiktoken.Encoding] = {}


def _get_tokenizer(encoding_name: str = "cl100k_base") -> tiktoken.Encoding:
    if encoding_name not in _tokenizer_cache:
        _tokenizer_cache[encoding_name] = tiktoken.get_encoding(encoding_name)
    return _tokenizer_cache[encoding_name]


def text_to_tokens(text: str, encoding_name: str = "cl100k_base") -> list[int]:
    if not text:
        return []
    return _get_tokenizer(encoding_name).encode(text)


def tokens_to_text(tokens: list[int], encoding_name: str = "cl100k_base") -> str:
    if not tokens:
        return ""
    return _get_tokenizer(encoding_name).decode(tokens)


def create_sentence_based_overlapping_chunks(
    full_text: str,
    chunk_size_tokens: int,
    overlap_tokens: int,
    encoding_name: str = "cl100k_base",
    language: str = "en",
) -> list[str]:
    """Split text into chunks that respect sentence boundaries with token-based overlap.

    Args:
        full_text: The text to chunk.
        chunk_size_tokens: Maximum tokens per chunk.
        overlap_tokens: Number of overlap tokens between consecutive chunks.
        encoding_name: Tiktoken encoding name.
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
) -> list[str]:
    """Convenience wrapper around create_sentence_based_overlapping_chunks."""
    return create_sentence_based_overlapping_chunks(
        text,
        chunk_size_tokens=desired_chunk_size,
        overlap_tokens=desired_overlap,
    )
