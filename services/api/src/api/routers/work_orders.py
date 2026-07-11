"""Work-order surface — file work orders and drive them through their lifecycle.

Open to org members to file + read; status/assignment/task edits are org-admin
(the agent org's configuration). Filing a work order optionally kicks off the
assigned supervisor agent via the run service (a queued run the worker drives).
"""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.work_order import WorkOrder
from api.schemas.work_order import (
    EntryRead,
    TaskRead,
    TasksSet,
    WorkOrderAssign,
    WorkOrderCreate,
    WorkOrderDetail,
    WorkOrderRead,
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
    return WorkOrderRead.model_validate(wo)


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
        title=body.title, body=body.body, priority=body.priority,
        assigned_agent_id=body.assigned_agent_id, created_by_profile_id=ctx.user.profile_id,
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
    detail.tasks = [TaskRead.model_validate(t) for t in tasks]
    detail.entries = [EntryRead.model_validate(e) for e in entries]
    detail.progress = svc.progress(tasks)
    return detail


@router.patch("/{wo_id}/status", response_model=WorkOrderRead)
async def set_status(
    wo_id: uuid.UUID,
    body: WorkOrderStatusUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WorkOrderRead:
    try:
        wo = await WorkOrderService(session, ctx.org_id).set_status(wo_id, body.status)
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
        wo = await WorkOrderService(session, ctx.org_id).assign(wo_id, body.assigned_agent_id)
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
        tasks = await WorkOrderService(session, ctx.org_id).set_tasks(
            wo_id, [t.model_dump() for t in body.tasks]
        )
    except WorkOrderError as exc:
        _raise_http(exc)
    return [TaskRead.model_validate(t) for t in tasks]
