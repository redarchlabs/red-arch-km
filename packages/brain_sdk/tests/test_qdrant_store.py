"""Tests for the Qdrant chunk-read paths (ranked search + document expansion)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore


def _point(point_id: str, **payload: Any) -> SimpleNamespace:
    return SimpleNamespace(id=point_id, score=payload.pop("score", 0.5), payload=payload)


@pytest.fixture
def store() -> QdrantVectorStore:
    with patch("brain_sdk.vector_store.qdrant_store.QdrantClient"):
        return QdrantVectorStore(url="http://qdrant:6333", dimension=4)


class TestSearchProjection:
    def test_section_survives_the_payload_projection(self, store: QdrantVectorStore) -> None:
        """Callers label passages and build citation deep-links from ``section``;
        dropping it here silently strips every passage's heading."""
        client = MagicMock()
        client.query_points.return_value = SimpleNamespace(
            points=[
                _point(
                    "c1",
                    score=0.8,
                    text="Six ships make up the fleet.",
                    section="The Ship Fleet › Quick comparison",
                    chunk_order=1,
                    document_key="dk",
                    document_title="Fleet",
                )
            ]
        )
        store._client = client  # type: ignore[assignment]
        results = store.search("t1", [0.1] * 4, limit=5)
        assert results[0].payload["section"] == "The Ship Fleet › Quick comparison"
        assert results[0].payload["chunk_order"] == 1

    def test_missing_section_is_none_not_empty_string(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.query_points.return_value = SimpleNamespace(points=[_point("c1", text="prose")])
        store._client = client  # type: ignore[assignment]
        assert store.search("t1", [0.1] * 4)[0].payload["section"] is None


class TestListDocumentChunks:
    def test_returns_chunks_in_reading_order_unscored(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.return_value = (
            [
                _point("c2", text="second", chunk_order=2, document_key="dk"),
                _point("c0", text="first", chunk_order=0, document_key="dk"),
                _point("c1", text="middle", chunk_order=1, document_key="dk"),
            ],
            None,
        )
        store._client = client  # type: ignore[assignment]
        results = store.list_document_chunks("t1", "dk")
        assert [r.payload["text"] for r in results] == ["first", "middle", "second"]
        # Not vector matches, so they carry no similarity score.
        assert {r.score for r in results} == {0.0}

    def test_scopes_to_the_document_and_reapplies_visibility_filters(self, store: QdrantVectorStore) -> None:
        """Expansion must not widen what a caller can see: the same access-key and
        tag conditions as ranked search are applied, plus the document key."""
        client = MagicMock()
        client.scroll.return_value = ([], None)
        store._client = client  # type: ignore[assignment]
        store.list_document_chunks(
            "t1",
            "dk",
            access_keys=[7],
            required_tags=["folder:hr"],
            any_tags=["folder:hr", "folder:ops"],
        )
        kwargs = client.scroll.call_args.kwargs
        assert kwargs["collection_name"] == "t1-chunks"
        rendered = [c.model_dump() for c in kwargs["scroll_filter"].must]
        keys = [c["key"] for c in rendered]
        assert keys.count("tags") == 2  # required (AND) + any (OR)
        assert "access_keys" in keys
        assert {"key": "document_key", "match": {"value": "dk"}}.items() <= (
            next(c for c in rendered if c["key"] == "document_key").items()
        )

    def test_missing_chunk_order_sorts_first_without_raising(self, store: QdrantVectorStore) -> None:
        """OCR/plain-text docs predating ordered chunks must not break expansion."""
        client = MagicMock()
        client.scroll.return_value = (
            [_point("c1", text="ordered", chunk_order=1), _point("c0", text="unordered")],
            None,
        )
        store._client = client  # type: ignore[assignment]
        assert [r.payload["text"] for r in store.list_document_chunks("t1", "dk")] == ["unordered", "ordered"]
