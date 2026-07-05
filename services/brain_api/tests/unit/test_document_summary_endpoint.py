"""Unit tests for the brain-api document-summary endpoint (vector store mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from brain_api.routers.ingest import get_document_summary
from brain_sdk.vector_store.protocol import SearchResult
from fastapi import HTTPException, status


@pytest.mark.asyncio
async def test_returns_summary_and_tree_when_record_exists() -> None:
    stores = MagicMock()
    stores.vector.get_document_record.return_value = SearchResult(
        id="doc-1",
        score=0.0,
        payload={
            "summary": "final",
            "summary_tree": {"summary": "final", "children": [{"summary": "leaf", "children": []}]},
        },
    )

    result = await get_document_summary("t1", "dk1", stores=stores, _api_key="x")

    stores.vector.get_document_record.assert_called_once_with("t1", "dk1")
    assert result["document_key"] == "dk1"
    assert result["summary"] == "final"
    assert result["summary_tree"]["children"][0]["summary"] == "leaf"


@pytest.mark.asyncio
async def test_404_when_record_missing() -> None:
    stores = MagicMock()
    stores.vector.get_document_record.return_value = None

    with pytest.raises(HTTPException) as exc:
        await get_document_summary("t1", "missing", stores=stores, _api_key="x")
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_500_when_store_raises() -> None:
    stores = MagicMock()
    stores.vector.get_document_record.side_effect = RuntimeError("qdrant down")

    with pytest.raises(HTTPException) as exc:
        await get_document_summary("t1", "dk1", stores=stores, _api_key="x")
    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
