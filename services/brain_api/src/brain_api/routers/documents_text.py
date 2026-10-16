"""Lexical search and whole-document reads — the grep-and-open half of retrieval.

``/vector-search`` finds passages *about* a question. These find the passages that
contain specific words, and read a document from start to finish, so an agent can
work over the knowledge base the way it would over a folder of files. Both apply
the caller's access keys and folder tags to every chunk they touch, exactly as
ranked search does (see :mod:`brain_sdk.document_text`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from brain_sdk.document_text import DEFAULT_READ_CHARS, MAX_READ_CHARS, DocumentTextReader, ReadScope
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from brain_api.auth import require_api_key
from brain_api.limits import MAX_ACCESS_KEYS
from brain_api.stores import Stores, get_stores

logger = logging.getLogger(__name__)
router = APIRouter()

_ACCESS_KEYS_DOC = "Permission masks (MatchAny). Absent/null = unrestricted; [] = nothing is readable."
_FOLDER_TAGS_DOC = "Folder tags (folder:<id>), ORed. Absent/null = every folder; [] = no folder is readable."


class _Scoped(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    access_keys: list[int] | None = Field(default=None, max_length=MAX_ACCESS_KEYS, description=_ACCESS_KEYS_DOC)
    tags: list[str] = Field(default_factory=list)
    folder_tags: list[str] | None = Field(default=None, description=_FOLDER_TAGS_DOC)

    def scope(self) -> ReadScope:
        return ReadScope(access_keys=self.access_keys, required_tags=self.tags or None, any_tags=self.folder_tags)


class TextSearchRequest(_Scoped):
    query: str = Field(min_length=1, max_length=500)
    # all: every word in one passage; any: at least one; phrase: the words adjacent, in order.
    mode: Literal["all", "any", "phrase"] = "all"
    max_documents: int = Field(default=10, ge=1, le=25)
    snippets_per_document: int = Field(default=3, ge=1, le=10)


class DocumentReadRequest(_Scoped):
    document_key: str = Field(min_length=1, max_length=255)
    start_chunk: int = Field(default=0, ge=0)
    max_chars: int = Field(default=DEFAULT_READ_CHARS, ge=500, le=MAX_READ_CHARS)


def _reader(stores: Annotated[Stores, Depends(get_stores)]) -> DocumentTextReader:
    return DocumentTextReader(stores.vector)


@router.post("/text-search")
async def text_search(
    body: TextSearchRequest,
    reader: Annotated[DocumentTextReader, Depends(_reader)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            reader.grep,
            body.tenant_id,
            body.query,
            scope=body.scope(),
            mode=body.mode,
            max_documents=body.max_documents,
            snippets_per_document=body.snippets_per_document,
        )
    except Exception:
        logger.exception("Text search failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Text search failed") from None


@router.post("/document-read")
async def document_read(
    body: DocumentReadRequest,
    reader: Annotated[DocumentTextReader, Depends(_reader)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            reader.read,
            body.tenant_id,
            body.document_key,
            scope=body.scope(),
            start_chunk=body.start_chunk,
            max_chars=body.max_chars,
        )
    except Exception:
        logger.exception("Document read failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Document read failed") from None
    if result is None:
        # Missing and unreadable are one answer: a caller learns nothing about a
        # document it may not see, not even that it exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return result
