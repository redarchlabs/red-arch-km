"""brain-api ``/text-search`` and ``/document-read`` (reader mocked).

The reader's behaviour is pinned in brain_sdk and against a real Qdrant; these pin
the HTTP contract around it: the caller's scope reaches the reader intact (an
explicit ``[]`` stays ``[]``), a missing or unreadable document is one 404, and a
store failure is a generic 500 that leaks nothing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from brain_api.routers.documents_text import DocumentReadRequest, TextSearchRequest, document_read, text_search
from brain_sdk.document_text import MAX_READ_CHARS
from fastapi import HTTPException, status
from pydantic import ValidationError


def _search(**overrides: object) -> TextSearchRequest:
    return TextSearchRequest(**{"tenant_id": "t1", "query": "PMP-2041-X", **overrides})


def _read(**overrides: object) -> DocumentReadRequest:
    return DocumentReadRequest(**{"tenant_id": "t1", "document_key": "pumps", **overrides})


class TestTextSearch:
    async def test_passes_query_mode_and_scope_through(self) -> None:
        reader = MagicMock()
        reader.grep.return_value = {"documents": []}
        body = _search(mode="phrase", access_keys=[0, 7], tags=["hr"], folder_tags=["folder:a"], max_documents=3)

        assert await text_search(body, reader=reader, _api_key="k") == {"documents": []}

        args, kwargs = reader.grep.call_args
        assert args == ("t1", "PMP-2041-X")
        assert kwargs["mode"] == "phrase" and kwargs["max_documents"] == 3
        scope = kwargs["scope"]
        assert (scope.access_keys, scope.required_tags, scope.any_tags) == ([0, 7], ["hr"], ["folder:a"])

    async def test_an_explicit_empty_scope_stays_empty(self) -> None:
        """Folded into None it would read the whole tenant."""
        reader = MagicMock()
        reader.grep.return_value = {"documents": []}
        await text_search(_search(access_keys=[], folder_tags=[]), reader=reader, _api_key="k")
        scope = reader.grep.call_args.kwargs["scope"]
        assert scope.access_keys == [] and scope.any_tags == [] and scope.is_empty

    async def test_store_failure_is_a_generic_500(self) -> None:
        reader = MagicMock()
        reader.grep.side_effect = RuntimeError("qdrant at 10.0.0.5 refused")
        with pytest.raises(HTTPException) as exc:
            await text_search(_search(), reader=reader, _api_key="k")
        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "10.0.0.5" not in str(exc.value.detail)

    def test_unknown_mode_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _search(mode="regex")


class TestDocumentRead:
    async def test_returns_the_read(self) -> None:
        reader = MagicMock()
        reader.read.return_value = {"text": "whole doc"}
        out = await document_read(_read(start_chunk=4, max_chars=2000), reader=reader, _api_key="k")
        assert out == {"text": "whole doc"}
        args, kwargs = reader.read.call_args
        assert args == ("t1", "pumps")
        assert kwargs["start_chunk"] == 4 and kwargs["max_chars"] == 2000

    async def test_missing_or_unreadable_is_404(self) -> None:
        reader = MagicMock()
        reader.read.return_value = None
        with pytest.raises(HTTPException) as exc:
            await document_read(_read(), reader=reader, _api_key="k")
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    async def test_store_failure_is_a_generic_500(self) -> None:
        reader = MagicMock()
        reader.read.side_effect = RuntimeError("boom")
        with pytest.raises(HTTPException) as exc:
            await document_read(_read(), reader=reader, _api_key="k")
        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.parametrize("bad", [{"start_chunk": -1}, {"max_chars": MAX_READ_CHARS + 1}, {"max_chars": 10}])
    def test_out_of_range_paging_is_refused(self, bad: dict[str, int]) -> None:
        with pytest.raises(ValidationError):
            _read(**bad)
