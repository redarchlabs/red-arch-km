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
import uuid
from collections.abc import Iterator, Mapping, Sequence
from typing import Annotated, Any

from brain_sdk.facts.agent import AgentContext, FactAgent
from brain_sdk.facts.gaps import GapStatus, assess_gap, build_gap
from brain_sdk.facts.models import now_iso
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


class GapStatusRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    gap_id: str = Field(min_length=1, max_length=64)
    status: GapStatus


class GapReExtractRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    gap_id: str = Field(min_length=1, max_length=64)
    limit: int = Field(default=5, ge=1, le=25)
    access_keys: list[int] = Field(default_factory=list)


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


def _capture_gap(
    stores: Stores,
    *,
    tenant_id: str,
    question: str,
    answer: str,
    evidence: Sequence[Mapping[str, Any]],
) -> None:
    """Record a knowledge gap when the fact tools came up empty (best-effort).

    A failure here must never surface to the user — capture is observability,
    not part of answering.
    """
    try:
        assessment = assess_gap(evidence)
        if not assessment.is_gap:
            return
        gap = build_gap(
            gap_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            question=question,
            answer=answer,
            assessment=assessment,
            now=now_iso(),
        )
        stores.fact_store.record_gap(gap)
        logger.info("Recorded knowledge gap for tenant %s: %r", tenant_id, question)
    except Exception:  # noqa: BLE001 - capture is advisory
        logger.warning("Knowledge-gap capture failed", exc_info=True)


@router.post("/agent/ask")
async def agent_ask(
    body: AgentAskRequest,
    agent: Annotated[FactAgent, Depends(_agent)],
    stores: Annotated[Stores, Depends(get_stores)],
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
    await asyncio.to_thread(
        _capture_gap,
        stores,
        tenant_id=body.tenant_id,
        question=body.query,
        answer=result.answer,
        evidence=result.evidence,
    )
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
    stores: Annotated[Stores, Depends(get_stores)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> StreamingResponse:
    """Streaming agentic query via SSE.

    Event types: ``thought`` | ``tool_call`` | ``tool_result`` | ``final`` | ``error``.
    """
    ctx = _context(body)
    history = _history(body)

    def event_generator() -> Iterator[str]:
        # Reconstruct the evidence trail from tool_result events so we can assess
        # a knowledge gap once the stream finalises (mirrors AgentResult.evidence).
        evidence: list[dict[str, Any]] = []
        try:
            for event in agent.stream(body.query, ctx, history=history):
                if event.get("type") == "tool_result":
                    evidence.append({"tool": event.get("tool"), "result": event.get("records") or []})
                elif event.get("type") == "final":
                    _capture_gap(
                        stores,
                        tenant_id=body.tenant_id,
                        question=body.query,
                        answer=str(event.get("answer", "")),
                        evidence=evidence,
                    )
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


def _gap_dict(gap: Any) -> dict[str, Any]:
    return {
        "gap_id": gap.gap_id,
        "question": gap.question,
        "answer": gap.answer,
        "tools_used": list(gap.tools_used),
        "fact_rows": gap.fact_rows,
        "occurrences": gap.occurrences,
        "status": str(gap.status),
        "created_at": gap.created_at,
        "last_seen_at": gap.last_seen_at,
    }


@router.get("/agent/gaps")
async def list_gaps(
    tenant_id: str,
    stores: Annotated[Stores, Depends(get_stores)],
    _api_key: Annotated[str, Depends(require_api_key)],
    limit: int = 100,
) -> dict[str, Any]:
    """Open knowledge gaps — questions the fact store could not answer — ranked
    by how often they recur, so the operator sees the highest-value gaps first."""
    stores.ensure_fact_schema()
    gaps = await asyncio.to_thread(
        stores.fact_store.list_open_gaps, tenant_id, limit=max(1, min(limit, 500))
    )
    return {"tenant_id": tenant_id, "gaps": [_gap_dict(g) for g in gaps]}


@router.post("/agent/gaps/status")
async def set_gap_status(
    body: GapStatusRequest,
    stores: Annotated[Stores, Depends(get_stores)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    """Resolve or dismiss a gap (e.g. after re-extraction closed it)."""
    stores.ensure_fact_schema()
    resolved_at = now_iso() if body.status is not GapStatus.OPEN else None
    ok = await asyncio.to_thread(
        stores.fact_store.set_gap_status,
        body.tenant_id,
        body.gap_id,
        body.status,
        resolved_at=resolved_at,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap not found")
    return {"gap_id": body.gap_id, "status": str(body.status)}


@router.post("/agent/gaps/re-extract")
async def suggest_re_extract(
    body: GapReExtractRequest,
    stores: Annotated[Stores, Depends(get_stores)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    """Suggest which documents to re-extract to close a gap.

    Passage search over the gap's question finds documents whose *text* is
    relevant — the fact store missed them, but RAG can still locate them.
    Re-ingesting those documents runs them through the (now type-/structure-aware)
    fact pipeline, converting the buried passage into queryable claims. The
    caller re-ingests via the normal ingest path and then marks the gap resolved.
    """
    stores.ensure_fact_schema()
    gap = await asyncio.to_thread(stores.fact_store.get_gap, body.tenant_id, body.gap_id)
    if gap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap not found")

    hits = await asyncio.to_thread(
        stores.search_passages,
        gap.question,
        limit=body.limit,
        tenant_id=body.tenant_id,
        access_keys=tuple(body.access_keys),
    )
    # Collapse to distinct documents, preserving best-match order.
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for hit in hits:
        key = str(hit.get("document_key", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "document_key": key,
                "document_title": hit.get("document_title", "Untitled"),
                "score": hit.get("score"),
            }
        )
    return {"gap_id": body.gap_id, "question": gap.question, "candidate_documents": candidates}
