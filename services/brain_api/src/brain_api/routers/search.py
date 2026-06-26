"""Vector search and hybrid query endpoints.

Blocking service calls are dispatched via `asyncio.to_thread` so the
event loop stays free for other requests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from brain_api.auth import require_api_key
from brain_api.config import BrainAPISettings
from brain_api.services.search_service import SearchService
from brain_api.stores import Stores, get_stores

logger = logging.getLogger(__name__)
router = APIRouter()


class VectorSearchRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=5000)
    limit: int = Field(default=5, ge=1, le=50)
    access_keys: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class VectorChatRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=5000)
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    access_keys: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    use_knowledge_graph: bool = True
    chunk_limit: int = Field(default=5, ge=1, le=20)


def _get_service(stores: Annotated[Stores, Depends(get_stores)]) -> SearchService:
    return SearchService(stores, BrainAPISettings())  # type: ignore[call-arg]


@router.post("/vector-search")
async def vector_search(
    body: VectorSearchRequest,
    service: Annotated[SearchService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            service.vector_search,
            tenant_id=body.tenant_id,
            query=body.query,
            limit=body.limit,
            access_keys=body.access_keys or None,
            tags=body.tags,
        )
    except Exception:
        logger.exception("Vector search failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed",
        ) from None


@router.post("/vector-chat")
async def vector_chat(
    body: VectorChatRequest,
    service: Annotated[SearchService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            service.vector_chat,
            tenant_id=body.tenant_id,
            query=body.query,
            chat_history=body.chat_history,
            access_keys=body.access_keys or None,
            tags=body.tags,
            use_knowledge_graph=body.use_knowledge_graph,
            chunk_limit=body.chunk_limit,
        )
    except Exception:
        logger.exception("Vector chat failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chat failed",
        ) from None
