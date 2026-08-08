"""Approvals, questions, and the escalation inbox — the human end of an agent run.

Org-admin gated. Three distinct things land here and they are not interchangeable:
approving/denying a pending tool call resumes or fails a run that already chose its
action; *answering* a question hands an agent information it could not get for
itself and resumes the same turn with that answer as the tool's result; the
notifications list is read-only history of what agents bubbled up.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.models.agent import Agent
from api.models.agent_run import AgentQuestion
from api.repositories.agent_questions import AgentQuestionRepository
from api.schemas.agent_run import (
    AnswerRequest,
    AnswerResult,
    ApprovalRead,
    DeclineRequest,
    NotificationRead,
    QuestionRead,
    UnreadCount,
)
from api.services.agents import questions as question_service
from api.services.agents.approvals import (
    ApprovalNotFoundError,
    ApprovalService,
    NotificationService,
)

router = APIRouter()


@router.get("/approvals", response_model=list[ApprovalRead])
async def list_approvals(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ApprovalRead]:
    approvals = await ApprovalService(session, ctx.org_id).list_pending()
    # One batch query maps each parked run to the workflow it blocks (if any),
    # so an approval row can deep-link to /workflows/{wf}/runs?run={run}.
    links: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID | None]] = {}
    run_ids = {a.run_id for a in approvals}
    if run_ids:
        from sqlalchemy import select

        from api.models.agent_run import AgentRun

        rows = await session.execute(
            select(AgentRun.id, AgentRun.workflow_run_id, AgentRun.input).where(
                AgentRun.id.in_(run_ids), AgentRun.workflow_run_id.is_not(None)
            )
        )
        for rid, wf_run_id, run_input in rows.all():
            raw = ((run_input or {}).get("workflow") or {}).get("workflow_id")
            try:
                wf_id = uuid.UUID(str(raw)) if raw else None
            except ValueError:
                wf_id = None
            links[rid] = (wf_run_id, wf_id)
    return [
        ApprovalRead.model_validate(a).model_copy(
            update={
                "workflow_run_id": links.get(a.run_id, (None, None))[0],
                "workflow_id": links.get(a.run_id, (None, None))[1],
            }
        )
        for a in approvals
    ]


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRead)
async def approve(
    approval_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ApprovalRead:
    try:
        approval = await ApprovalService(session, ctx.org_id).approve(approval_id, ctx.user.profile_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ApprovalRead.model_validate(approval)


@router.post("/approvals/{approval_id}/deny", response_model=ApprovalRead)
async def deny(
    approval_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ApprovalRead:
    try:
        approval = await ApprovalService(session, ctx.org_id).deny(approval_id, ctx.user.profile_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ApprovalRead.model_validate(approval)


async def _to_question_read(session: AsyncSession, rows: list[AgentQuestion]) -> list[QuestionRead]:
    """Attach agent display names in one batch query rather than per row."""
    agent_ids = {a for row in rows for a in (row.asked_by_agent_id, row.target_agent_id) if a is not None}
    names: dict[uuid.UUID, str] = {}
    if agent_ids:
        result = await session.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
        names = dict(result.all())  # type: ignore[arg-type]
    return [
        QuestionRead.model_validate(row).model_copy(
            update={
                "asked_by": names.get(row.asked_by_agent_id) if row.asked_by_agent_id else None,
                "target_agent": names.get(row.target_agent_id) if row.target_agent_id else None,
            }
        )
        for row in rows
    ]


@router.get("/questions", response_model=list[QuestionRead])
async def list_questions(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[QuestionRead]:
    """Questions waiting on a person.

    Peer-to-peer consults are deliberately excluded: another agent is already on
    the hook to answer those, and surfacing them would invite a human to answer a
    question that is not theirs — which would leave the consulted agent's run
    running with nobody listening.
    """
    rows = await AgentQuestionRepository(session, ctx.org_id).list_pending(audience="human")
    return await _to_question_read(session, rows)


@router.post("/questions/{question_id}/answer", response_model=AnswerResult)
async def answer_question(
    question_id: uuid.UUID,
    body: AnswerRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AnswerResult:
    try:
        outcome = await question_service.answer_question(
            session, ctx.org_id, question_id, answer=body.answer, by_profile_id=ctx.user.profile_id
        )
    except question_service.QuestionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except question_service.QuestionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    read = (await _to_question_read(session, [outcome.question]))[0]
    return AnswerResult(question=read, resumed=outcome.resumed)


@router.post("/questions/{question_id}/decline", response_model=AnswerResult)
async def decline_question(
    question_id: uuid.UUID,
    body: DeclineRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AnswerResult:
    """Unblock the agent without answering — it is told to use its own judgement."""
    try:
        outcome = await question_service.decline_question(
            session, ctx.org_id, question_id, reason=body.reason, by_profile_id=ctx.user.profile_id
        )
    except question_service.QuestionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except question_service.QuestionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    read = (await _to_question_read(session, [outcome.question]))[0]
    return AnswerResult(question=read, resumed=outcome.resumed)


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    unresolved_only: Annotated[bool, Query()] = False,
) -> list[NotificationRead]:
    items = await NotificationService(session, ctx.org_id).list(unresolved_only=unresolved_only)
    return [NotificationRead.model_validate(n) for n in items]


@router.get("/notifications/unread-count", response_model=UnreadCount)
async def unread_count(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> UnreadCount:
    return UnreadCount(unread=await NotificationService(session, ctx.org_id).unread_count())


@router.post("/notifications/{notification_id}/{action}", response_model=NotificationRead)
async def update_notification(
    notification_id: uuid.UUID,
    action: str,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> NotificationRead:
    status_map = {"read": "read", "resolve": "resolved"}
    if action not in status_map:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "action must be 'read' or 'resolve'")
    try:
        notification = await NotificationService(session, ctx.org_id).set_status(notification_id, status_map[action])
    except ApprovalNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return NotificationRead.model_validate(notification)
