"""Reranker client for the `/rerank` HTTP API.

One request shape covers every backend KM2 is likely to point at, because they
all copied Cohere's:

    POST {base_url}/rerank
    {"model": …, "query": …, "documents": [str, …], "top_n": int}

* llama.cpp   ``llama-server --reranking`` with a bge-reranker GGUF (fully local)
* TEI         HuggingFace text-embeddings-inference in reranker mode
* Jina        api.jina.ai/v1
* Cohere      api.cohere.com/v2

Responses differ only in packaging: Cohere/Jina/llama.cpp return
``{"results": [{"index", "relevance_score"}]}`` while TEI returns a bare list of
``{"index", "score"}``. Both are parsed here so switching backends stays a URL
change.

Deliberately raises rather than degrading on failure — the caller (retrieval)
decides that reranking is optional and falls back to dense order. Swallowing the
error here would make a permanently mis-configured endpoint invisible.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from brain_sdk.reranking.protocol import RerankResult

logger = logging.getLogger(__name__)

# A cross-encoder is small and local in the default deployment, but it still runs
# one forward pass per candidate; a cold model load can eat several seconds.
_DEFAULT_TIMEOUT = 20.0


class RerankError(RuntimeError):
    """The rerank endpoint could not be reached or returned an unusable body."""


class HTTPReranker:
    """Rerank via any Cohere-compatible ``/rerank`` endpoint.

    ``base_url`` is the API root *without* the ``/rerank`` suffix — e.g.
    ``http://127.0.0.1:8096/v1``. ``api_key`` is optional: a self-hosted server
    authenticates nothing, a hosted one needs a bearer token.

    The client is constructed once and reused, so repeated queries keep the
    connection pool warm — the same reason the embedding provider holds its
    client rather than building one per call.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_key: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("HTTPReranker requires a base_url")
        self._url = base_url.strip().rstrip("/") + "/rerank"
        self._model = model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(timeout=timeout, headers=headers)

    def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[RerankResult]:
        """Score every document against the query; best first."""
        if not documents:
            return []

        payload: dict[str, Any] = {"query": query, "documents": documents}
        # llama.cpp serves whatever model it loaded and ignores this, but hosted
        # providers require it — send it only when configured.
        if self._model:
            payload["model"] = self._model
        if top_n is not None:
            payload["top_n"] = top_n

        try:
            response = self._client.post(self._url, json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as e:
            raise RerankError(f"rerank request to {self._url} failed: {e}") from e
        except ValueError as e:  # malformed JSON
            raise RerankError(f"rerank endpoint {self._url} returned non-JSON: {e}") from e

        results = self._parse(body, candidates=len(documents))
        # Providers are *supposed* to sort by score, but ordering is the entire
        # contract here — sort rather than trust it.
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_n] if top_n is not None else results

    def _parse(self, body: Any, *, candidates: int) -> list[RerankResult]:
        """Pull (index, score) pairs out of either response shape."""
        rows = body.get("results") if isinstance(body, dict) else body
        if not isinstance(rows, list):
            raise RerankError(f"rerank endpoint {self._url} returned no results list")

        out: list[RerankResult] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = row.get("index")
            # Cohere/Jina/llama.cpp call it relevance_score; TEI calls it score.
            score = row.get("relevance_score", row.get("score"))
            if not isinstance(index, int) or not isinstance(score, int | float):
                continue
            # A bad index would silently re-point a citation at the wrong passage.
            if not 0 <= index < candidates:
                logger.warning("rerank returned out-of-range index %s; dropping", index)
                continue
            out.append(RerankResult(index=index, score=float(score)))

        if not out:
            raise RerankError(f"rerank endpoint {self._url} returned no usable scores")
        return out

    def close(self) -> None:
        self._client.close()
