"""Tests for the Cohere-shaped /rerank HTTP client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from brain_sdk.reranking.http_reranker import HTTPReranker, RerankError


def _reranker(handler: Any, **kwargs: Any) -> HTTPReranker:
    """A reranker whose transport is a stub, so no socket is opened."""
    rr = HTTPReranker(base_url="http://localhost:8096/v1", **kwargs)
    rr._client = httpx.Client(transport=httpx.MockTransport(handler), headers=rr._client.headers)
    return rr


class TestRequest:
    def test_posts_to_the_rerank_path(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})

        _reranker(handler).rerank("q", ["a"])
        assert seen["url"] == "http://localhost:8096/v1/rerank"

    def test_trailing_slash_does_not_double_up(self) -> None:
        rr = HTTPReranker(base_url="http://localhost:8096/v1/")
        assert rr._url == "http://localhost:8096/v1/rerank"

    def test_sends_query_documents_and_top_n(self) -> None:
        import json

        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})

        _reranker(handler, model="bge-reranker-v2-m3").rerank("crew size", ["a", "b"], top_n=1)
        assert seen == {
            "query": "crew size",
            "documents": ["a", "b"],
            "model": "bge-reranker-v2-m3",
            "top_n": 1,
        }

    def test_model_omitted_when_unset(self) -> None:
        """llama.cpp serves whatever it loaded and rejects nothing, but sending an
        empty model name to a hosted provider is an error."""
        import json

        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})

        _reranker(handler).rerank("q", ["a"])
        assert "model" not in seen

    def test_api_key_becomes_a_bearer_token(self) -> None:
        rr = HTTPReranker(base_url="http://x/v1", api_key="secret")
        assert rr._client.headers["Authorization"] == "Bearer secret"

    def test_no_auth_header_without_a_key(self) -> None:
        rr = HTTPReranker(base_url="http://x/v1")
        assert "Authorization" not in rr._client.headers

    def test_empty_documents_short_circuits(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
            raise AssertionError("no request should be made for an empty candidate list")

        assert _reranker(handler).rerank("q", []) == []

    def test_blank_base_url_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            HTTPReranker(base_url="   ")


class TestResponseParsing:
    def test_cohere_shape(self) -> None:
        """Cohere / Jina / llama.cpp: {"results": [{index, relevance_score}]}."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.12},
                        {"index": 2, "relevance_score": 0.91},
                    ]
                },
            )

        out = _reranker(handler).rerank("q", ["a", "b", "c"])
        assert [(r.index, r.score) for r in out] == [(2, 0.91), (0, 0.12)]

    def test_tei_shape(self) -> None:
        """text-embeddings-inference returns a bare list keyed on `score`."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"index": 1, "score": 0.8}, {"index": 0, "score": 0.2}])

        out = _reranker(handler).rerank("q", ["a", "b"])
        assert [(r.index, r.score) for r in out] == [(1, 0.8), (0, 0.2)]

    def test_results_are_sorted_even_when_the_server_is_not(self) -> None:
        """Ordering is the entire contract — it is not taken on trust."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.1},
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 2, "relevance_score": 0.5},
                    ]
                },
            )

        out = _reranker(handler).rerank("q", ["a", "b", "c"])
        assert [r.index for r in out] == [1, 2, 0]

    def test_top_n_truncates_locally(self) -> None:
        """A server that ignores top_n must not widen the caller's context."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.9},
                        {"index": 1, "relevance_score": 0.8},
                        {"index": 2, "relevance_score": 0.7},
                    ]
                },
            )

        assert len(_reranker(handler).rerank("q", ["a", "b", "c"], top_n=2)) == 2

    def test_out_of_range_index_is_dropped(self) -> None:
        """A bad index would re-point a citation at the wrong passage."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 99, "relevance_score": 0.99},
                        {"index": 1, "relevance_score": 0.4},
                    ]
                },
            )

        out = _reranker(handler).rerank("q", ["a", "b"])
        assert [r.index for r in out] == [1]

    def test_malformed_rows_are_skipped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": "one", "relevance_score": 0.9},
                        {"relevance_score": 0.8},
                        {"index": 1, "relevance_score": 0.4},
                    ]
                },
            )

        out = _reranker(handler).rerank("q", ["a", "b"])
        assert [r.index for r in out] == [1]


class TestFailures:
    """Errors are raised, not swallowed: retrieval decides to fall back, and a
    permanently mis-configured endpoint must not look like a working one."""

    def test_http_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(RerankError, match="failed"):
            _reranker(handler).rerank("q", ["a"])

    def test_connection_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(RerankError, match="failed"):
            _reranker(handler).rerank("q", ["a"])

    def test_non_json_body_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        with pytest.raises(RerankError, match="non-JSON"):
            _reranker(handler).rerank("q", ["a"])

    def test_missing_results_list_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"detail": "unsupported"})

        with pytest.raises(RerankError, match="no results list"):
            _reranker(handler).rerank("q", ["a"])

    def test_all_rows_unusable_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [{"index": 42, "relevance_score": 0.9}]})

        with pytest.raises(RerankError, match="no usable scores"):
            _reranker(handler).rerank("q", ["a"])
