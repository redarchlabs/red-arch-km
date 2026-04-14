"""Chunk and hierarchical document summarisation using OpenAI.

The summariser produces two kinds of output:

1. **Per-chunk summaries** — one LLM call per chunk, run in parallel via a
   thread pool so ingesting a 50-chunk document isn't 50 serial round-trips.
2. **Document-level summary** — truly hierarchical: chunk summaries are
   grouped, each group is summarised, the group summaries are grouped and
   summarised again, and so on until one final summary emerges. This keeps
   every LLM call under the model's context window even for huge documents.
"""

from __future__ import annotations

import concurrent.futures
import logging

from openai import OpenAI

logger = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = (
    "Summarize the following text concisely, preserving key facts and entities. "
    "Output only the summary, no preamble."
)

_GROUP_SUMMARY_PROMPT = (
    "You are given a list of summaries from one document. "
    "Write one concise summary that captures the combined themes, "
    "key facts, and important entities. Output only the summary."
)


def _tokens_estimate(text: str) -> int:
    """Cheap token count heuristic; good enough for group-size decisions."""
    return max(1, len(text) // 4)


class ChunkSummarizer:
    """Summarise chunks and produce a true hierarchical document summary."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5-mini",
        *,
        max_workers: int = 8,
        group_size: int = 10,
        max_depth: int = 5,
        chunk_summary_max_tokens: int = 300,
        group_summary_max_tokens: int = 600,
    ) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._max_workers = max_workers
        self._group_size = group_size
        self._max_depth = max_depth
        self._chunk_max = chunk_summary_max_tokens
        self._group_max = group_summary_max_tokens

    def summarize_chunk(self, chunk_text: str) -> str:
        """Summarise a single text chunk."""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SUMMARIZE_PROMPT},
                {"role": "user", "content": chunk_text},
            ],
            max_tokens=self._chunk_max,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    def summarize_chunks(self, chunks: list[str]) -> list[str]:
        """Summarise many chunks in parallel, preserving input order."""
        if not chunks:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as exe:
            # executor.map preserves order — important so summary[i] matches chunk[i].
            return list(exe.map(self._safe_summarize, chunks))

    def _safe_summarize(self, chunk: str) -> str:
        try:
            return self.summarize_chunk(chunk)
        except Exception as e:
            logger.warning("Chunk summary failed (len=%d): %s", len(chunk), e)
            # Fall back to a truncated chunk so downstream embedding still has content.
            return chunk[:1000]

    def summarize_document(self, chunk_summaries: list[str]) -> str:
        """Recursively condense chunk summaries into one document summary.

        Each level groups the current list in fixed-size groups, summarises
        each group in parallel, and becomes the next level. Terminates when
        the list is of length 1 or after `max_depth` levels.
        """
        if not chunk_summaries:
            return ""
        if len(chunk_summaries) == 1:
            return chunk_summaries[0]

        level = chunk_summaries
        for depth in range(self._max_depth):
            groups = [
                level[i : i + self._group_size]
                for i in range(0, len(level), self._group_size)
            ]
            logger.debug(
                "Hierarchical summary depth=%d: %d items -> %d groups",
                depth, len(level), len(groups),
            )
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers
            ) as exe:
                level = list(exe.map(self._summarize_group, groups))
            if len(level) <= 1:
                break
        return level[0] if level else ""

    def _summarize_group(self, summaries: list[str]) -> str:
        """Summarise one group of summaries into a single summary.

        Handles three edge cases:
          - single item → return as-is (skip LLM round-trip)
          - all items empty → return empty (don't send LLM a blank prompt)
          - only one non-empty item → return it (same saving as single-item)
        """
        if len(summaries) == 1:
            return summaries[0]

        non_empty = [s for s in summaries if s and s.strip()]
        if not non_empty:
            logger.warning("_summarize_group: all %d items empty", len(summaries))
            return ""
        if len(non_empty) == 1:
            return non_empty[0]

        combined = "\n\n".join(f"- {s}" for s in non_empty)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _GROUP_SUMMARY_PROMPT},
                    {"role": "user", "content": combined},
                ],
                max_tokens=self._group_max,
                temperature=0.3,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error("Group summary failed (%d items): %s", len(summaries), e)
            # Degrade gracefully — pick the longest summary as a proxy for
            # "most informative" so downstream processing continues.
            return max(non_empty, key=_tokens_estimate, default="")

    # Backwards-compat alias for older callers.
    def create_document_summary(self, chunk_summaries: list[str]) -> str:
        return self.summarize_document(chunk_summaries)
