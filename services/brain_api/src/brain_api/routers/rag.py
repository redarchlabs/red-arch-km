"""RAG pipeline endpoints with streaming support.

The non-streaming `/ask` endpoint wraps its blocking call in
`asyncio.to_thread` to keep the event loop responsive. The streaming
`/ask/stream` endpoint uses FastAPI's StreamingResponse, which consumes
a sync generator in a thread pool by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from brain_api.auth import require_api_key
from brain_api.config import BrainAPISettings
from brain_api.services.search_service import SearchService
from brain_api.stores import Stores, get_stores

logger = logging.getLogger(__name__)
router = APIRouter()


class AskRequest(BaseModel):
    """Retrieval request.

    SECURITY (defense-in-depth note): brain-api sits behind a single shared
    ``X-API-Key`` (``BRAIN_API_KEY``) and *trusts* the caller-supplied
    ``tenant_id`` and ``access_keys`` — the key holder (the app backend) is
    responsible for scoping these to the authenticated end user before calling.
    That makes ``BRAIN_API_KEY`` a high-value secret: anyone holding it can read
    ANY tenant's data by passing an arbitrary ``tenant_id``. It must never be
    exposed to browsers/end users, only to server-side callers that have already
    authenticated + authorized the request. ``tenant_id``/``access_keys`` are
    bounded/typed below (length + int list) as a minimal guard, but the trust
    boundary is the API key itself.
    """

    tenant_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=5000)
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    access_keys: list[int] = Field(default_factory=list, max_length=256)
    tags: list[str] = Field(default_factory=list)
    folder_tags: list[str] = Field(
        default_factory=list,
        description="Folder-membership tags (folder:<id>); ORed to scope retrieval to a set of folders.",
    )
    use_knowledge_graph: bool = True


def _get_service(stores: Annotated[Stores, Depends(get_stores)]) -> SearchService:
    return SearchService(stores, BrainAPISettings())  # type: ignore[call-arg]


@router.post("/ask")
async def ask(
    body: AskRequest,
    service: Annotated[SearchService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    """Non-streaming RAG query."""
    try:
        return await asyncio.to_thread(
            service.vector_chat,
            tenant_id=body.tenant_id,
            query=body.query,
            chat_history=body.chat_history,
            access_keys=body.access_keys or None,
            tags=body.tags,
            folder_tags=body.folder_tags or None,
            use_knowledge_graph=body.use_knowledge_graph,
        )
    except Exception:
        logger.exception("RAG ask failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RAG query failed",
        ) from None


@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    service: Annotated[SearchService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> StreamingResponse:
    """Streaming RAG query via Server-Sent Events.

    Event types emitted:
      - sources: list of document references retrieved
      - graph: list of graph triplets used as context
      - delta: incremental answer text fragment
      - done: terminal marker
      - error: terminal error marker
    """

    def event_generator() -> Iterator[str]:
        for event in service.vector_chat_stream(
            tenant_id=body.tenant_id,
            query=body.query,
            chat_history=body.chat_history,
            access_keys=body.access_keys or None,
            tags=body.tags,
            folder_tags=body.folder_tags or None,
            use_knowledge_graph=body.use_knowledge_graph,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )
