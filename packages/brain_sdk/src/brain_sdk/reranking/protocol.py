"""Protocol for rerankers.

A reranker scores a *query against each candidate passage jointly*, which is what
a bi-encoder embedding search cannot do: dense retrieval embeds the query and the
passage independently, so it matches on topical similarity and misses paraphrase.
"How many people can each ship handle?" and "**Standard Crew Complement:** 5,500
officers and crew" share no vocabulary and are not near neighbours in vector
space, yet one answers the other. A cross-encoder reads both together and scores
that pair directly.

The trade is cost: scoring is O(candidates) forward passes, so it cannot replace
retrieval — it re-orders a shortlist that dense search produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RerankResult:
    """One scored candidate.

    ``index`` refers to the position in the ``documents`` list passed to
    :meth:`Reranker.rerank`, so callers can map a result back to the hit it came
    from. ``score`` is provider-specific and only meaningful for ordering — it is
    not comparable across models, and not on the same scale as a cosine score.
    """

    index: int
    score: float


class Reranker(Protocol):
    """Interface for query/passage relevance scoring."""

    def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[RerankResult]:
        """Score ``documents`` against ``query``, best first.

        Returns at most ``top_n`` results when given. Results are ordered by
        descending score; callers should not assume every input is returned.
        """
        ...
