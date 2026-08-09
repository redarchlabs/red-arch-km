"""Interactive agent console (SSE) + run history — the member-facing surface.

Open to any org member: the agent acts with the agent's configured grants, so a
member can never do more through an agent than the org admin granted it. Config
(creating/editing agents) stays admin-only in ``routers/agents.py``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_access_streaming
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.dependencies import get_redis_client, get_tenant_db
from api.repositories.agent_questions import AgentQuestionRepository
from api.repositories.agent_run import AgentRunRepository
from api.schemas.agent_run import AgentRunRead, AgentRunStepRead, AnswerRequest, AnswerResult, QuestionRead
from api.services.agents import questions as question_service
from api.services.agents.console import AgentConsoleService
from api.services.agents.live import bus

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ConsoleMessage(BaseModel):
    role: str
    content: str


class ConsoleRequest(BaseModel):
    messages: list[ConsoleMessage] = Field(default_factory=list)
    # Documents pasted alongside the last message. Ids only: the bytes are loaded
    # server-side and only for a model that can actually look at them.
    document_ids: list[uuid.UUID] = Field(default_factory=list)


@router.post("/{agent_id}/console/stream")
async def agent_console_stream(
    agent_id: uuid.UUID,
    body: ConsoleRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access_streaming)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    # Deliberately the *streaming* auth dependency. The ordinary one takes
    # ``get_db``, and FastAPI exits yield-dependencies only after the response
    # completes — so for an SSE endpoint it pinned a pooled connection for the
    # entire stream, idle, to serve one membership lookup. (An earlier comment
    # here claimed the opposite; it was wrong.) The service likewise takes a
    # session only while it has work to do.
    factory = get_session_factory(settings)
    service = AgentConsoleService(ctx.org_id, settings, factory, ctx.user.profile_id, redis=get_redis_client(settings))
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def iterator() -> AsyncGenerator[bytes]:
        try:
            async for event in service.run_stream(agent_id, history, document_ids=body.document_ids):
                yield f"data: {json.dumps(event, default=str)}\n\n".encode()
        except Exception:  # noqa: BLE001 - never break the SSE frame contract
            yield b'data: {"type": "error", "error": "Stream failed"}\n\n'

    return StreamingResponse(iterator(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/runs/{run_id}/reply", response_model=AnswerResult)
async def reply_to_run(
    run_id: uuid.UUID,
    body: AnswerRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnswerResult:
    """Answer the question your own agent just asked you, from the console.

    Authorized by **ownership, not role**. ``POST /agents/questions/{id}/answer``
    is admin-only because it can settle any question in the org; this one can only
    settle a question raised by a run the caller personally started, which is the
    other half of the message they sent — not an administrative act. That is why
    this route exists rather than relaxing the admin gate on the other one.
    """
    run = await AgentRunRepository(session, ctx.org_id).get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if run.actor_user_id is None or run.actor_user_id != ctx.user.profile_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "that run is not yours to answer")

    pending = await AgentQuestionRepository(session, ctx.org_id).pending_for_asking_run(run_id)
    human = [q for q in pending if q.audience == "human"]
    if not human:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that run is not waiting on a question")

    try:
        outcome = await question_service.record_answer(
            session, ctx.org_id, human[0], answer=body.answer, by_profile_id=ctx.user.profile_id
        )
    except question_service.QuestionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    # Commit HERE, then wake. get_tenant_db commits in teardown — after the
    # response — so publishing before that would announce an answer the waiting
    # console could read before it exists, and it would resume with nothing.
    await session.commit()
    await bus.publish_run_event(
        get_redis_client(settings), ctx.org_id, run_id, {"type": bus.EVENT_ANSWER, "question_id": str(human[0].id)}
    )
    return AnswerResult(question=QuestionRead.model_validate(outcome.question), resumed=outcome.resumed)


@router.get("/{agent_id}/runs", response_model=list[AgentRunRead])
async def list_agent_runs(
    agent_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgentRunRead]:
    runs = await AgentRunRepository(session, ctx.org_id).list_runs(agent_id=agent_id)
    return [AgentRunRead.model_validate(r) for r in runs]


@router.get("/runs/{run_id}", response_model=AgentRunRead)
async def get_agent_run(
    run_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AgentRunRead:
    run = await AgentRunRepository(session, ctx.org_id).get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return AgentRunRead.model_validate(run)


@router.get("/runs/{run_id}/steps", response_model=list[AgentRunStepRead])
async def get_agent_run_steps(
    run_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AgentRunStepRead]:
    repo = AgentRunRepository(session, ctx.org_id)
    if await repo.get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return [AgentRunStepRead.model_validate(s) for s in await repo.list_steps(run_id)]
