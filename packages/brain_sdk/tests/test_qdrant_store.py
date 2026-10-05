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


class TestIterDocumentHeads:
    """Feeds the fact-graph Document-node backfill: one chunk per document (its
    first, ``chunk_order`` 0), paged until Qdrant reports no further cursor.

    Chunks rather than the document-level record, because ``update_metadata``
    re-tags and re-masks chunk payloads only — after a folder move the chunk
    carries the document's current folder tag and the document record does not.
    """

    def test_pages_through_first_chunks(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = [
            ([_point("c1", document_key="k1", tags=["folder:a"], access_keys=[0])], "cursor-2"),
            ([_point("c2", document_key="k2", tags=[], access_keys=[5])], None),
        ]
        store._client = client  # type: ignore[assignment]

        keys = [r.payload["document_key"] for r in store.iter_document_heads("t1", batch_size=1)]

        assert keys == ["k1", "k2"]
        first, second = client.scroll.call_args_list
        assert first.kwargs["collection_name"] == "t1-chunks"
        assert first.kwargs["offset"] is None and second.kwargs["offset"] == "cursor-2"
        conds = {c.key: c.match.value for c in first.kwargs["scroll_filter"].must}
        assert conds == {"tenant_id": "t1", "chunk_order": 0}

    def test_missing_collection_yields_nothing(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = RuntimeError("Collection `t1-chunks` doesn't exist!")
        store._client = client  # type: ignore[assignment]
        assert list(store.iter_document_heads("t1")) == []

    def test_other_errors_propagate(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = RuntimeError("connection refused")
        store._client = client  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="connection refused"):
            list(store.iter_document_heads("t1"))


class TestUpdateMetadataPublicSentinel:
    """Ingest stores a public document's chunks with ``access_keys: [0]`` and every
    mask-filtered search carries 0 (``MatchAny``). ``[]`` matches nothing, so a
    metadata write that stored ``[]`` made a public document vanish from every
    restricted reader's search after a move or permission change."""

    def _client(self) -> MagicMock:
        client = MagicMock()
        client.scroll.return_value = ([_point("c1"), _point("c2")], None)
        return client

    def test_empty_masks_are_stored_as_the_public_sentinel(self, store: QdrantVectorStore) -> None:
        client = self._client()
        store._client = client  # type: ignore[assignment]
        store.update_metadata("t1", "dk", tags=["folder:a"], access_keys=[])
        assert client.set_payload.call_args.kwargs["payload"] == {"tags": ["folder:a"], "access_keys": [0]}

    def test_real_masks_are_stored_as_given(self, store: QdrantVectorStore) -> None:
        client = self._client()
        store._client = client  # type: ignore[assignment]
        store.update_metadata("t1", "dk", access_keys=[5, 6])
        assert client.set_payload.call_args.kwargs["payload"] == {"access_keys": [5, 6]}

    def test_masks_left_alone_when_not_given(self, store: QdrantVectorStore) -> None:
        client = self._client()
        store._client = client  # type: ignore[assignment]
        store.update_metadata("t1", "dk", title="New")
        assert client.set_payload.call_args.kwargs["payload"] == {"document_title": "New"}


class TestRepairEmptyAccessKeys:
    """Rewrites chunks a buggy metadata write left with ``access_keys == []`` to the
    public sentinel ``[0]``. Idempotent: only an exact empty list is touched — not a
    missing field, not real masks — so a second run repairs nothing."""

    def test_only_exact_empty_lists_are_rewritten(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = [
            ([_point("c1", access_keys=[]), _point("c2", access_keys=[0])], "next"),
            ([_point("c3"), _point("c4", access_keys=[])], None),
        ]
        store._client = client  # type: ignore[assignment]

        result = store.repair_empty_access_keys("t1")

        assert result == {"scanned": 4, "repaired": 2}
        client.set_payload.assert_called_once()
        kwargs = client.set_payload.call_args.kwargs
        assert kwargs["collection_name"] == "t1-chunks"
        assert kwargs["payload"] == {"access_keys": [0]}
        assert kwargs["points"] == ["c1", "c4"]

    def test_nothing_to_repair_writes_nothing(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.return_value = ([_point("c1", access_keys=[0])], None)
        store._client = client  # type: ignore[assignment]
        assert store.repair_empty_access_keys("t1") == {"scanned": 1, "repaired": 0}
        client.set_payload.assert_not_called()

    def test_missing_collection_is_zero(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = RuntimeError("Collection `t1-chunks` doesn't exist!")
        store._client = client  # type: ignore[assignment]
        assert store.repair_empty_access_keys("t1") == {"scanned": 0, "repaired": 0}


class TestIterDocumentKeys:
    def test_pages_through_document_records(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = [
            ([_point("d1", document_key="k1")], "n"),
            ([_point("d2", document_key="k2"), _point("d3")], None),
        ]
        store._client = client  # type: ignore[assignment]
        assert list(store.iter_document_keys("t1")) == ["k1", "k2"]
        assert client.scroll.call_args_list[0].kwargs["collection_name"] == "t1-documents"

    def test_missing_collection_yields_nothing(self, store: QdrantVectorStore) -> None:
        client = MagicMock()
        client.scroll.side_effect = RuntimeError("Not found: Collection `t1-documents`")
        store._client = client  # type: ignore[assignment]
        assert list(store.iter_document_keys("t1")) == []
