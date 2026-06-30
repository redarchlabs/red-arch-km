"""Search routes — proxy to brain-api with permission-mask filtering applied."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.org import Org
from api.schemas.search import (
    ChatRequest,
    ChatResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from api.services.brain_client import BrainAPIClient
from api.services.permission_config import calculate_user_masks_from_membership

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_user_access_keys(session: AsyncSession, ctx: OrgContext) -> list[int] | None:
    """Return the user's access masks, or None for admins (unrestricted)."""
    if ctx.is_org_admin:
        return None
    org = await session.get(Org, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    return calculate_user_masks_from_membership(ctx.membership, org.permission_number)


@router.post("/", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchResponse:
    """Semantic search scoped to the user's accessible content."""
    access_keys = await _get_user_access_keys(session, ctx)

    client = BrainAPIClient(settings)
    result = await client.vector_search(
        tenant_id=str(ctx.org_id),
        query=body.query,
        limit=body.limit,
        access_keys=access_keys,
        tags=body.tags,
    )

    hits = [
        SearchResult(
            id=h["id"],
            score=h["score"],
            text=h["payload"].get("text", ""),
            document_id=h["payload"].get("document_id", ""),
            document_key=h["payload"].get("document_key", ""),
            document_title=h["payload"].get("document_title", ""),
            chunk_order=h["payload"].get("chunk_order", 0),
        )
        for h in result.get("hits", [])
    ]
    return SearchResponse(hits=hits, total=result.get("total", len(hits)))


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatResponse:
    """Hybrid RAG chat scoped to the user's accessible content."""
    access_keys = await _get_user_access_keys(session, ctx)

    client = BrainAPIClient(settings)
    result = await client.vector_chat(
        tenant_id=str(ctx.org_id),
        query=body.query,
        chat_history=body.chat_history,
        access_keys=access_keys,
        tags=body.tags,
        use_knowledge_graph=body.use_knowledge_graph,
    )

    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        graph_context=result.get("graph_context", []),
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Streaming RAG chat — proxies brain-api's SSE stream to the UI.

    The UI uses fetch (not EventSource) so auth headers can flow through;
    we in turn stream bytes from brain-api straight to the client without
    reparsing, preserving SSE framing + timing.
    """
    access_keys = await _get_user_access_keys(session, ctx)
    client = BrainAPIClient(settings)

    async def iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in client.vector_chat_stream(
                tenant_id=str(ctx.org_id),
                query=body.query,
                chat_history=body.chat_history,
                access_keys=access_keys,
                tags=body.tags,
                use_knowledge_graph=body.use_knowledge_graph,
            ):
                yield chunk
        except Exception:
            logger.exception("Chat stream failed")
            # Emit a synthetic SSE error so the client terminates gracefully
            # instead of hanging on a silently-closed connection.
            yield b'data: {"type": "error", "message": "Stream failed"}\n\n'

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
