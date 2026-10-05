"""``/api/v1/search`` — semantic search + RAG chat over the knowledge base.

Wraps :class:`BrainAPIClient`. What a key can retrieve depends on its access mode
(``api_key_access_keys`` / ``api_key_folder_scope``):

* an **org key** has org-wide content visibility (``None``) — results are not
  filtered by permission masks; the ``search:read`` scope is the gate;
* a **scoped key** retrieves with the masks of its own dimension assignments,
  resolved on every request, so passages and graph facts its assignments cannot
  see are filtered out inside brain-api. A key limited to folders searches only
  those folders (and their subfolders): ``folder_ids`` outside them is a ``404``,
  none means all of them, and ``folder_tags`` is always sent — for search AND chat,
  which brain-api applies to graph facts too.

Folder scoping via ``folder_ids`` applies on top of either. A folder-limited key
whose folders are all hidden gets an empty answer without brain-api being called
(brain-api would also answer an explicit empty ``folder_tags`` with nothing; an
absent one means "no folder filter").
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, require_scope
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.schemas.search import (
    ChatRequest,
    ChatResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from api.services.brain_client import BrainAPIClient
from api.services.org_llm import org_default_llm_model
from api.services.search_access import api_key_access_keys, api_key_folder_scope, folder_tags

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
    # Before any upstream call: fail closed first.
    access_keys = api_key_access_keys(principal)
    folders = api_key_folder_scope(principal, body.folder_ids)
    if folders == []:
        return SearchResponse(hits=[], total=0)
    client = BrainAPIClient(settings)
    result = await client.vector_search(
        tenant_id=str(principal.org_id),
        query=body.query,
        limit=body.limit,
        access_keys=access_keys,
        tags=body.tags,
        folder_tags=folder_tags(folders),
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
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChatResponse:
    """Hybrid RAG chat: an answer grounded in the org's knowledge base.

    Requires the ``search:read`` scope."""
    access_keys = api_key_access_keys(principal)
    folders = api_key_folder_scope(principal, body.folder_ids)
    if folders == []:
        return ChatResponse(answer="", sources=[], graph_context=[])
    client = BrainAPIClient(settings)
    result = await client.vector_chat(
        tenant_id=str(principal.org_id),
        query=body.query,
        chat_history=body.chat_history,
        access_keys=access_keys,
        tags=body.tags,
        folder_tags=folder_tags(folders),
        use_knowledge_graph=body.use_knowledge_graph,
        # Org-pinned answer model (local vs 3rd-party); None = brain-api default.
        model=await org_default_llm_model(session, principal.org_id),
    )
    return ChatResponse(
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        graph_context=result.get("graph_context", []),
    )
