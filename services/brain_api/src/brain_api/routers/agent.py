"""Agentic query endpoints (the fact engine's query surface).

Unlike the legacy `/api/v1/ask` RAG path (single vector query + graph sprinkle),
these endpoints run an iterative tool-using loop over the reified-claim fact
store and stream the agent's trace. The legacy path stays available; this is a
new, additive surface (see docs/KNOWLEDGE_ENGINE.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any

from brain_sdk.facts.agent import AgentContext, FactAgent
from brain_sdk.llm.protocol import LLMMessage
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from brain_api.auth import require_api_key
from brain_api.stores import Stores, get_stores

logger = logging.getLogger(__name__)
router = APIRouter()


class AgentAskRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=5000)
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    access_keys: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DigestRebuildRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)


def _history(body: AgentAskRequest) -> list[LLMMessage]:
    out: list[LLMMessage] = []
    for turn in body.chat_history[-10:]:
        role, content = turn.get("role", "user"), turn.get("content", "")
        if role in ("user", "assistant") and content:
            out.append(LLMMessage(role, content))
    return out


def _agent(stores: Annotated[Stores, Depends(get_stores)]) -> FactAgent:
    stores.ensure_fact_schema()
    return stores.make_fact_agent()


def _context(body: AgentAskRequest) -> AgentContext:
    return AgentContext(
        tenant_id=body.tenant_id,
        access_keys=tuple(body.access_keys),
        tags=tuple(body.tags),
    )


@router.post("/agent/ask")
async def agent_ask(
    body: AgentAskRequest,
    agent: Annotated[FactAgent, Depends(_agent)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    """Non-streaming agentic query. Returns the answer, citations, and trace."""
    try:
        result = await asyncio.to_thread(agent.run, body.query, _context(body), history=_history(body))
    except Exception:
        logger.exception("Agentic ask failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agentic query failed",
        ) from None
    return {
        "answer": result.answer,
        "citations": result.citations,
        "unsupported_citations": result.unsupported_citations,
        "evidence": result.evidence,
        "iterations": result.iterations,
    }


@router.post("/agent/digest/rebuild")
async def agent_digest_rebuild(
    body: DigestRebuildRequest,
    stores: Annotated[Stores, Depends(get_stores)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    """(Re)build the GraphRAG community-summary digest for a tenant."""
    stores.ensure_fact_schema()
    builder = stores.make_digest_builder()
    try:
        communities = await asyncio.to_thread(builder.build, body.tenant_id)
    except Exception:
        logger.exception("Digest rebuild failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Digest rebuild failed",
        ) from None
    return {"tenant_id": body.tenant_id, "communities": communities}


@router.post("/agent/ask/stream")
async def agent_ask_stream(
    body: AgentAskRequest,
    agent: Annotated[FactAgent, Depends(_agent)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> StreamingResponse:
    """Streaming agentic query via SSE.

    Event types: ``thought`` | ``tool_call`` | ``tool_result`` | ``final`` | ``error``.
    """
    ctx = _context(body)
    history = _history(body)

    def event_generator() -> Iterator[str]:
        try:
            for event in agent.stream(body.query, ctx, history=history):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception:
            logger.exception("Agentic stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Agentic query failed'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
