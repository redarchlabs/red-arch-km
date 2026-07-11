"""``/api/v1/workflows`` — list workflows, run them, and inspect runs.

Authoring stays on the first-party admin surface; the enterprise API can list
workflows and trigger their PUBLISHED version. A key holding ``workflows:run`` may
run any workflow in its org — the per-workflow ``run_permission`` (which gates
*users*) does not apply to a service key; the scope is the gate. Runs go through
the same ``execute_workflow_run`` helper as the UI, so the don't-trust-client-data
and input-coercion guards are identical.

Read endpoints run on the org-scoped RLS session (defence-in-depth). ``run`` uses
the privileged session (matching the internal router) because the workflow engine
performs cross-cutting writes; every repository call is explicitly org-scoped.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.repositories.workflow import WorkflowRepository
from api.schemas.workflow import (
    ManualRunRequest,
    ManualRunResult,
    WorkflowRead,
    WorkflowRunRead,
    WorkflowRunStepRead,
)
from api.services.workflow.manual_run import execute_workflow_run, resolve_published_version
from api.services.workflow.service import WorkflowNotFoundError, WorkflowService

router = APIRouter()


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("workflows:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[WorkflowRead]:
    """List the org's workflows. Requires the ``workflows:read`` scope."""
    items = await WorkflowRepository(session, principal.org_id).list_all()
    return [WorkflowRead.model_validate(w) for w in items]


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("workflows:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> WorkflowRead:
    """Fetch one workflow's metadata. Requires the ``workflows:read`` scope."""
    wf = await WorkflowRepository(session, principal.org_id).get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    return WorkflowRead.model_validate(wf)


@router.post("/{workflow_id}/run", response_model=ManualRunResult)
async def run_workflow(
    workflow_id: uuid.UUID,
    body: ManualRunRequest,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("workflows:run"))],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ManualRunResult:
    """Run the workflow's published version for real (performs side effects).

    Requires the ``workflows:run`` scope, which runs ANY workflow in the org."""
    wf = await WorkflowRepository(session, principal.org_id).get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    version = await resolve_published_version(session, principal.org_id, wf)
    return await execute_workflow_run(
        session,
        principal.org_id,
        wf,
        version,
        request=body,
        actor_user_id=None,
        settings=settings,
    )


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunRead])
async def list_runs(
    workflow_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("workflows:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WorkflowRunRead]:
    """List a workflow's recent runs. Requires the ``workflows:read`` scope."""
    try:
        runs = await WorkflowService(session, principal.org_id).runs(workflow_id, limit=limit)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [WorkflowRunRead.model_validate(r) for r in runs]


@router.get("/runs/{run_id}/steps", response_model=list[WorkflowRunStepRead])
async def list_run_steps(
    run_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("workflows:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[WorkflowRunStepRead]:
    """List the per-step trace for a single run. Requires the ``workflows:read`` scope."""
    steps = await WorkflowService(session, principal.org_id).run_steps(run_id)
    return [WorkflowRunStepRead.model_validate(s) for s in steps]
