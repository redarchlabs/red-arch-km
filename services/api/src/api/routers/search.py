"""Search routes — proxy to brain-api with permission-mask filtering applied."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.schemas.search import (
    AgentChatRequest,
    AgentChatResponse,
    ChatRequest,
    ChatResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from api.services.brain_client import BrainAPIClient
from api.services.search_access import folder_tags as _folder_tags
from api.services.search_access import resolve_user_access_keys as _get_user_access_keys

logger = logging.getLogger(__name__)
router = APIRouter()


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
        folder_tags=_folder_tags(body.folder_ids),
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
        folder_tags=_folder_tags(body.folder_ids),
        use_knowledge_graph=body.use_knowledge_graph,
    )

    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        graph_context=result.get("graph_context", []),
    )


@router.post("/chat/agent", response_model=AgentChatResponse)
async def agent_chat(
    body: AgentChatRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentChatResponse:
    """Agentic (fact-engine) chat — iterative tool loop, scoped to the user's access."""
    access_keys = await _get_user_access_keys(session, ctx)

    client = BrainAPIClient(settings)
    result = await client.agent_ask(
        tenant_id=str(ctx.org_id),
        query=body.query,
        chat_history=body.chat_history,
        access_keys=access_keys,
        tags=body.tags,
    )
    return AgentChatResponse(
        answer=result.get("answer", ""),
        citations=result.get("citations", []),
        unsupported_citations=result.get("unsupported_citations", []),
        evidence=result.get("evidence", []),
        iterations=result.get("iterations", 0),
    )


@router.post("/chat/agent/stream")
async def agent_chat_stream(
    body: AgentChatRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Streaming agentic chat — proxies brain-api's agent SSE trace to the UI."""
    access_keys = await _get_user_access_keys(session, ctx)
    client = BrainAPIClient(settings)

    async def iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in client.agent_ask_stream(
                tenant_id=str(ctx.org_id),
                query=body.query,
                chat_history=body.chat_history,
                access_keys=access_keys,
                tags=body.tags,
            ):
                yield chunk
        except Exception:
            logger.exception("Agent chat stream failed")
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
                folder_tags=_folder_tags(body.folder_ids),
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
