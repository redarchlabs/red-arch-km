"""Workflow authoring + run-monitoring routes.

Authoring runs on the privileged ``get_db`` session (like entity definitions)
and requires org admin. Run-monitoring reads back the partitioned run/step
tables for the dashboard.
"""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.repositories.workflow import (
    WorkflowConnectionRepository,
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
    WorkflowVersionRepository,
)
from api.schemas.workflow import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionUpdate,
    InboundEndpointCreate,
    InboundEndpointCreated,
    InboundEndpointRead,
    ManualRunRequest,
    ManualRunResult,
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
from api.services.email import EmailSender
from api.services.workflow.actions import SIDE_EFFECTING_ACTIONS
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.permissions import can_run
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


def _raise(exc: Exception) -> NoReturn:
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


# --------------------------------------------------------------------------- #
# Connector credentials (org-admin) — secrets encrypted at rest, never returned
# --------------------------------------------------------------------------- #
def _conn_read(conn: object) -> ConnectionRead:
    read = ConnectionRead.model_validate(conn)
    read.has_secret = bool(getattr(conn, "secret_encrypted", None))
    return read


@router.get("/connections", response_model=list[ConnectionRead])
async def list_connections(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[ConnectionRead]:
    conns = await WorkflowConnectionRepository(session, ctx.org_id).list_all()
    return [_conn_read(c) for c in conns]


@router.post("/connections", response_model=ConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionRead:
    from api.services.crypto import encrypt_secret

    secret_encrypted = (
        encrypt_secret(body.secret, settings.org_encryption_key.get_secret_value())
        if body.secret
        else None
    )
    conn = await WorkflowConnectionRepository(session, ctx.org_id).create(
        name=body.name,
        kind=body.kind,
        base_url=body.base_url,
        auth_type=body.auth_type,
        secret_encrypted=secret_encrypted,
        config=body.config,
    )
    return _conn_read(conn)


@router.patch("/connections/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionRead:
    from api.services.crypto import encrypt_secret

    repo = WorkflowConnectionRepository(session, ctx.org_id)
    conn = await repo.get(connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    # Only re-encrypt when a new secret is supplied; omitting it keeps the old one.
    secret_encrypted = (
        encrypt_secret(body.secret, settings.org_encryption_key.get_secret_value())
        if body.secret
        else None
    )
    await repo.update(
        conn,
        name=body.name,
        base_url=body.base_url,
        auth_type=body.auth_type,
        secret_encrypted=secret_encrypted,
        config=body.config,
    )
    return _conn_read(conn)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    repo = WorkflowConnectionRepository(session, ctx.org_id)
    conn = await repo.get(connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    await repo.delete(conn)


# --------------------------------------------------------------------------- #
# Inbound webhook endpoints (org-admin) — public URLs that start a workflow run
# --------------------------------------------------------------------------- #
@router.get("/inbound-endpoints", response_model=list[InboundEndpointRead])
async def list_inbound_endpoints(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[InboundEndpointRead]:
    items = await WorkflowInboundEndpointRepository(session, ctx.org_id).list_all()
    return [InboundEndpointRead.model_validate(e) for e in items]


@router.post("/inbound-endpoints", response_model=InboundEndpointCreated, status_code=status.HTTP_201_CREATED)
async def create_inbound_endpoint(
    body: InboundEndpointCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InboundEndpointCreated:
    import secrets

    from api.services.workflow.inbound import hash_token

    wf = await WorkflowRepository(session, ctx.org_id).get(body.workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    token = secrets.token_urlsafe(32)
    endpoint = await WorkflowInboundEndpointRepository(session, ctx.org_id).create(
        name=body.name, workflow_id=body.workflow_id, token_hash=hash_token(token)
    )
    # The token is shown ONCE (only its hash is stored). Build the callable URL.
    url = f"{settings.public_base_url.rstrip('/')}/api/inbound/{token}"
    read = InboundEndpointCreated.model_validate(endpoint)
    read.token = token
    read.url = url
    return read


@router.delete("/inbound-endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbound_endpoint(
    endpoint_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    repo = WorkflowInboundEndpointRepository(session, ctx.org_id)
    endpoint = await repo.get(endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="inbound endpoint not found")
    await repo.delete(endpoint)


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
    await repo.update(
        wf,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        run_permission=body.run_permission.model_dump(mode="json")
        if body.run_permission is not None
        else None,
    )
    return WorkflowRead.model_validate(wf)


@router.post("/{workflow_id}/run", response_model=ManualRunResult)
async def run_workflow(
    workflow_id: uuid.UUID,
    body: ManualRunRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ManualRunResult:
    """Run the workflow's PUBLISHED version for real against provided inputs.

    Gated by the workflow's ``run_permission`` (org admins always; optionally
    widened to any member or specific roles/groups). Unlike the dry-run test,
    this performs real side effects and records a ``workflow_run``.
    """
    wf = await WorkflowRepository(session, ctx.org_id).get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    if not can_run(ctx, wf.run_permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to run this workflow",
        )
    if wf.active_version_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workflow has no published version")
    version = await WorkflowVersionRepository(session, ctx.org_id).get(wf.active_version_id)
    if version is None or version.status != "published":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workflow has no published version")

    allowlist = tuple(settings.workflow_webhook_allowlist or ())
    dispatcher = WorkflowDispatchService(
        session,
        webhook_allowlist=allowlist,
        public_base_url=settings.public_base_url,
        email_sender=EmailSender(settings),
        org_encryption_key=settings.org_encryption_key.get_secret_value(),
    )

    # SECURITY: never trust client-supplied record data for a manual run.
    #  * With a record_id, load before/after from the real entity table scoped
    #    to this org + the workflow's entity — a cross-org/cross-entity id can't
    #    resolve, and the client's before/after are ignored entirely.
    #  * Without a record_id, refuse to run any side-effecting action (email/
    #    webhook/form invite) on free-form client data — otherwise a member with
    #    any_member run_permission could email/webhook arbitrary fabricated data.
    if body.record_id is not None:
        record = await dispatcher.load_trigger_record(
            ctx.org_id, wf.entity_definition_id, body.record_id
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="record not found for this workflow's entity",
            )
        before, after = record, record
    else:
        # Legacy `action` nodes and v2 `task` nodes both carry an action_type;
        # inspect both so a side-effecting step in either vocabulary is caught.
        action_types = {
            node.get("data", {}).get("action_type")
            for node in version.definition.get("nodes", [])
            if node.get("type") in ("action", "task")
        }
        if action_types & SIDE_EFFECTING_ACTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This workflow performs external actions (email/webhook/form). "
                    "Provide a record_id so it runs against a real record."
                ),
            )
        before, after = body.before, body.after

    run, executed = await dispatcher.run_version_manually(
        ctx.org_id,
        wf,
        version,
        operation=body.operation,
        record_id=body.record_id,
        before=before,
        after=after,
        actor_user_id=ctx.user.profile_id,
    )
    return ManualRunResult(
        run_id=run.id,
        status=run.status,
        conditions_matched=bool(run.conditions_matched),
        actions_executed=executed,
        error=run.error,
    )


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
