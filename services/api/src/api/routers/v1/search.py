"""``/api/v1/search`` — semantic search + RAG chat over the knowledge base.

Wraps :class:`BrainAPIClient`. An org service key has org-wide content visibility
(``service_key_access_keys`` → ``None``), so results are not filtered by
per-user permission masks; the ``search:read`` scope is the gate. Folder scoping
via ``folder_ids`` still applies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.auth.api_key import ApiKeyPrincipal, require_scope
from api.config import Settings, get_settings
from api.schemas.search import (
    ChatRequest,
    ChatResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from api.services.brain_client import BrainAPIClient
from api.services.search_access import folder_tags, service_key_access_keys

router = APIRouter()


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("search:read"))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchResponse:
    """Semantic (vector) search over the org's knowledge base.

    Requires the ``search:read`` scope. Data is served by brain-api addressed by
    tenant id, so no local DB session is opened here."""
    client = BrainAPIClient(settings)
    result = await client.vector_search(
        tenant_id=str(principal.org_id),
        query=body.query,
        limit=body.limit,
        access_keys=service_key_access_keys(),
        tags=body.tags,
        folder_tags=folder_tags(body.folder_ids),
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
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("search:read"))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatResponse:
    """Hybrid RAG chat: an answer grounded in the org's knowledge base.

    Requires the ``search:read`` scope."""
    client = BrainAPIClient(settings)
    result = await client.vector_chat(
        tenant_id=str(principal.org_id),
        query=body.query,
        chat_history=body.chat_history,
        access_keys=service_key_access_keys(),
        tags=body.tags,
        folder_tags=folder_tags(body.folder_ids),
        use_knowledge_graph=body.use_knowledge_graph,
    )
    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        graph_context=result.get("graph_context", []),
    )
