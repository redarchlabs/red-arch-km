"""RAG pipeline endpoints with streaming support."""

from __future__ import annotations

import json
import logging
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
    tenant_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=5000)
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    access_keys: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
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
        return service.vector_chat(
            tenant_id=body.tenant_id,
            query=body.query,
            chat_history=body.chat_history,
            access_keys=body.access_keys or None,
            tags=body.tags,
            use_knowledge_graph=body.use_knowledge_graph,
        )
    except Exception:
        logger.exception("RAG ask failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RAG query failed",
        )


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

    def event_generator():  # type: ignore[no-untyped-def]
        for event in service.vector_chat_stream(
            tenant_id=body.tenant_id,
            query=body.query,
            chat_history=body.chat_history,
            access_keys=body.access_keys or None,
            tags=body.tags,
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
