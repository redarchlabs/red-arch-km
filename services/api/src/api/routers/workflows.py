"""Workflow authoring + run-monitoring routes.

Authoring runs on the privileged ``get_db`` session (like entity definitions)
and requires org admin. Run-monitoring reads back the partitioned run/step
tables for the dashboard.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_db
from api.repositories.workflow import WorkflowRepository, WorkflowVersionRepository
from api.schemas.workflow import (
    VersionSaveRequest,
    WorkflowCreate,
    WorkflowRead,
    WorkflowRunRead,
    WorkflowRunStepRead,
    WorkflowTestRequest,
    WorkflowTestResult,
    WorkflowUpdate,
    WorkflowVersionRead,
)
from api.services.workflow.service import (
    WorkflowConflictError,
    WorkflowNotFoundError,
    WorkflowService,
)

router = APIRouter()

_ERROR_STATUS = {
    WorkflowNotFoundError: status.HTTP_404_NOT_FOUND,
    WorkflowConflictError: status.HTTP_409_CONFLICT,
}


def _raise(exc: Exception) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST), detail=str(exc)
    ) from exc


@router.get("/", response_model=list[WorkflowRead])
async def list_workflows(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[WorkflowRead]:
    items = await WorkflowRepository(session, ctx.org_id).list_all()
    return [WorkflowRead.model_validate(w) for w in items]


@router.post("/", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkflowRead:
    service = WorkflowService(session, ctx.org_id)
    try:
        wf = await service.create_workflow(
            name=body.name, entity_definition_id=body.entity_definition_id, description=body.description
        )
    except WorkflowNotFoundError as exc:
        _raise(exc)
    return WorkflowRead.model_validate(wf)


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkflowRead:
    wf = await WorkflowRepository(session, ctx.org_id).get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    return WorkflowRead.model_validate(wf)


@router.patch("/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkflowRead:
    repo = WorkflowRepository(session, ctx.org_id)
    wf = await repo.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    await repo.update(wf, name=body.name, description=body.description, enabled=body.enabled)
    return WorkflowRead.model_validate(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    repo = WorkflowRepository(session, ctx.org_id)
    wf = await repo.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    await repo.delete(wf)


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionRead])
async def list_versions(
    workflow_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[WorkflowVersionRead]:
    versions = await WorkflowVersionRepository(session, ctx.org_id).list_for_workflow(workflow_id)
    return [WorkflowVersionRead.model_validate(v) for v in versions]


@router.post("/{workflow_id}/versions", response_model=WorkflowVersionRead, status_code=status.HTTP_201_CREATED)
async def save_draft(
    workflow_id: uuid.UUID,
    body: VersionSaveRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkflowVersionRead:
    service = WorkflowService(session, ctx.org_id)
    try:
        version = await service.save_draft(workflow_id, body.definition)
    except WorkflowNotFoundError as exc:
        _raise(exc)
    return WorkflowVersionRead.model_validate(version)


@router.post("/{workflow_id}/versions/{version_id}/publish", response_model=WorkflowVersionRead)
async def publish_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkflowVersionRead:
    service = WorkflowService(session, ctx.org_id)
    try:
        version = await service.publish(workflow_id, version_id)
    except (WorkflowNotFoundError, WorkflowConflictError) as exc:
        _raise(exc)
    return WorkflowVersionRead.model_validate(version)


@router.post("/{workflow_id}/versions/{version_id}/test", response_model=WorkflowTestResult)
async def test_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    body: WorkflowTestRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkflowTestResult:
    service = WorkflowService(session, ctx.org_id)
    try:
        result = await service.test_version(
            version_id, operation=body.operation, before=body.before, after=body.after
        )
    except WorkflowNotFoundError as exc:
        _raise(exc)
    return WorkflowTestResult.model_validate(result)


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunRead])
async def list_runs(
    workflow_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WorkflowRunRead]:
    service = WorkflowService(session, ctx.org_id)
    try:
        runs = await service.runs(workflow_id, limit=limit)
    except WorkflowNotFoundError as exc:
        _raise(exc)
    return [WorkflowRunRead.model_validate(r) for r in runs]


@router.get("/runs/{run_id}/steps", response_model=list[WorkflowRunStepRead])
async def list_run_steps(
    run_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[WorkflowRunStepRead]:
    steps = await WorkflowService(session, ctx.org_id).run_steps(run_id)
    return [WorkflowRunStepRead.model_validate(s) for s in steps]
