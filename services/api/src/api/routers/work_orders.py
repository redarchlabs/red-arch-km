"""Work-order surface — file work orders and drive them through their lifecycle.

Open to org members to file + read; status/assignment/task edits are org-admin
(the agent org's configuration). Filing a work order optionally kicks off the
assigned supervisor agent via the run service (a queued run the worker drives).
"""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.dependencies import get_tenant_db
from api.models.work_order import WorkOrder
from api.schemas.work_order import (
    EntryPageRead,
    EntryRead,
    TaskRead,
    TasksSet,
    WorkOrderAssign,
    WorkOrderCreate,
    WorkOrderDetail,
    WorkOrderMap,
    WorkOrderModeUpdate,
    WorkOrderRead,
    WorkOrderReply,
    WorkOrderReviewLevelUpdate,
    WorkOrderStatusUpdate,
)
from api.services.agents.work_order_service import (
    WorkOrderError,
    WorkOrderNotFoundError,
    WorkOrderService,
    WorkOrderValidationError,
)

router = APIRouter()

_ERROR_STATUS = {
    WorkOrderNotFoundError: status.HTTP_404_NOT_FOUND,
    WorkOrderValidationError: status.HTTP_400_BAD_REQUEST,
}


def _raise_http(exc: WorkOrderError) -> NoReturn:
    raise HTTPException(_ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST), str(exc)) from exc


def _to_read(wo: WorkOrder) -> WorkOrderRead:
    return WorkOrderRead.model_validate(wo).model_copy(
        update={"allowed_transitions": WorkOrderService.allowed_transitions(wo.status)}
    )


@router.get("/", response_model=list[WorkOrderRead])
async def list_work_orders(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[WorkOrderRead]:
    return [_to_read(w) for w in await WorkOrderService(session, ctx.org_id).list_work_orders()]


@router.post("/", response_model=WorkOrderRead, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    body: WorkOrderCreate,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    wo = await WorkOrderService(session, ctx.org_id).create_work_order(
        title=body.title,
        body=body.body,
        priority=body.priority,
        mode=body.mode,
        review_level=body.review_level,
        assigned_agent_id=body.assigned_agent_id,
        created_by_profile_id=ctx.user.profile_id,
    )
    return _to_read(wo)


@router.get("/{wo_id}", response_model=WorkOrderDetail)
async def get_work_order(
    wo_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderDetail:
    svc = WorkOrderService(session, ctx.org_id)
    try:
        wo = await svc.get_work_order(wo_id)
    except WorkOrderError as exc:
        _raise_http(exc)
    tasks = await svc.list_tasks(wo_id)
    entries = await svc.list_entries(wo_id)
    detail = WorkOrderDetail.model_validate(wo)
    detail.allowed_transitions = svc.allowed_transitions(wo.status)
    detail.tasks = [TaskRead.model_validate(t) for t in tasks]
    detail.entries = [EntryRead.model_validate(e) for e in entries]
    detail.progress = svc.progress(tasks)
    return detail


@router.get("/{wo_id}/entries", response_model=EntryPageRead)
async def list_entries_page(
    wo_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[uuid.UUID | None, Query()] = None,
) -> EntryPageRead:
    """A page of diary, newest first, returned in reading order.

    The page loads the tail and walks backwards as the reader scrolls up, so an
    order with a long agent transcript does not render hundreds of Markdown
    blocks nobody looks at.
    """
    try:
        page = await WorkOrderService(session, ctx.org_id).list_entries_page(wo_id, limit=limit, before=before)
    except WorkOrderError as exc:
        _raise_http(exc)
    return EntryPageRead(entries=[EntryRead.model_validate(e) for e in page.entries], has_more=page.has_more)


@router.get("/{wo_id}/map", response_model=WorkOrderMap)
async def interaction_map(
    wo_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderMap:
    """Who did what under this order, and who is waiting on whom."""
    try:
        return await WorkOrderService(session, ctx.org_id).interaction_map(wo_id)
    except WorkOrderError as exc:
        _raise_http(exc)


@router.patch("/{wo_id}/status", response_model=WorkOrderRead)
async def set_status(
    wo_id: uuid.UUID,
    body: WorkOrderStatusUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    try:
        # Starting an assigned order queues a run for its agent, which reads the
        # knowledge base as the person who started it — never wider.
        wo = await WorkOrderService(session, ctx.org_id).set_status(
            wo_id, body.status, actor_profile_id=ctx.user.profile_id
        )
    except WorkOrderError as exc:
        _raise_http(exc)
    return _to_read(wo)


@router.patch("/{wo_id}/assignment", response_model=WorkOrderRead)
async def assign(
    wo_id: uuid.UUID,
    body: WorkOrderAssign,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    try:
        # Assigning an already-started order dispatches it, so the actor is needed
        # here for the same reason as on status: the run reads as that person.
        wo = await WorkOrderService(session, ctx.org_id).assign(
            wo_id, body.assigned_agent_id, actor_profile_id=ctx.user.profile_id
        )
    except WorkOrderError as exc:
        _raise_http(exc)
    return _to_read(wo)


@router.patch("/{wo_id}/mode", response_model=WorkOrderRead)
async def set_mode(
    wo_id: uuid.UUID,
    body: WorkOrderModeUpdate,
    # Admin: 'automatic' removes the human from every approval on this order.
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    try:
        wo = await WorkOrderService(session, ctx.org_id).set_mode(wo_id, body.mode)
    except WorkOrderError as exc:
        _raise_http(exc)
    return _to_read(wo)


@router.patch("/{wo_id}/review-level", response_model=WorkOrderRead)
async def set_review_level(
    wo_id: uuid.UUID,
    body: WorkOrderReviewLevelUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    try:
        wo = await WorkOrderService(session, ctx.org_id).set_review_level(wo_id, body.review_level)
    except WorkOrderError as exc:
        _raise_http(exc)
    return _to_read(wo)


@router.post("/{wo_id}/reply", response_model=WorkOrderRead)
async def reply(
    wo_id: uuid.UUID,
    body: WorkOrderReply,
    # Admin, like status and assignment: a reply can queue a run, and every other
    # route that starts an agent is admin-gated.
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    try:
        wo = await WorkOrderService(session, ctx.org_id).reply(wo_id, body.text, actor_profile_id=ctx.user.profile_id)
    except WorkOrderError as exc:
        _raise_http(exc)
    return _to_read(wo)


@router.put("/{wo_id}/tasks", response_model=list[TaskRead])
async def set_tasks(
    wo_id: uuid.UUID,
    body: TasksSet,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[TaskRead]:
    try:
        tasks = await WorkOrderService(session, ctx.org_id).set_tasks(wo_id, [t.model_dump() for t in body.tasks])
    except WorkOrderError as exc:
        _raise_http(exc)
    return [TaskRead.model_validate(t) for t in tasks]
