"""Approvals + escalation inbox — the human end of the authority "ask" tier.

Org-admin gated: approving/denying a pending tool call resumes or fails the parked
run; the notifications list surfaces bubbled escalations + review requests.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.schemas.agent_run import ApprovalRead, NotificationRead, UnreadCount
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
