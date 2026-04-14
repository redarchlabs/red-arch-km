"""Recursive chunk summarization using LLM."""

from __future__ import annotations

import logging

from openai import OpenAI

logger = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = (
    "Summarize the following text concisely, preserving key facts and entities. "
    "Output only the summary, no preamble."
)

_DOCUMENT_SUMMARY_PROMPT = (
    "You are given a list of chunk summaries from a document. "
    "Write a comprehensive document-level summary that captures the main themes, "
    "key facts, and important entities. Output only the summary."
)


class ChunkSummarizer:
    """Summarize text chunks and generate hierarchical document summaries."""

    def __init__(self, api_key: str, model: str = "gpt-4.1-mini") -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def summarize_chunk(self, chunk_text: str) -> str:
        """Summarize a single text chunk."""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SUMMARIZE_PROMPT},
                {"role": "user", "content": chunk_text},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    def summarize_chunks(self, chunks: list[str]) -> list[str]:
        """Summarize a list of text chunks."""
        return [self.summarize_chunk(c) for c in chunks]

    def create_document_summary(self, chunk_summaries: list[str]) -> str:
        """Create a hierarchical document summary from chunk summaries."""
        combined = "\n\n".join(
            f"Chunk {i + 1}: {s}" for i, s in enumerate(chunk_summaries)
        )
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _DOCUMENT_SUMMARY_PROMPT},
                {"role": "user", "content": combined},
            ],
            max_tokens=500,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
