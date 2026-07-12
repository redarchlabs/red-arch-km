"""Workflow authoring + run-monitoring routes.

Authoring runs on the privileged ``get_db`` session (like entity definitions)
and requires org admin. Run-monitoring reads back the partitioned run/step
tables for the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.dependencies import get_db
from api.repositories.workflow import (
    WorkflowConnectionRepository,
    WorkflowInboundEndpointRepository,
    WorkflowRepository,
    WorkflowVersionRepository,
)
from api.schemas.workflow import (
    CompleteTaskRequest,
    CompleteTaskResult,
    ConnectionCall,
    ConnectionCallResult,
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
    WorkflowRunActivityRead,
    WorkflowRunRead,
    WorkflowRunStepRead,
    WorkflowTestRequest,
    WorkflowTestResult,
    WorkflowUpdate,
    WorkflowVersionRead,
)
from api.services.email import EmailSender
from api.services.workflow.manual_run import execute_workflow_run, resolve_published_version
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
    raise HTTPException(status_code=_ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST), detail=str(exc)) from exc


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
        encrypt_secret(body.secret, settings.org_encryption_key.get_secret_value()) if body.secret else None
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
        encrypt_secret(body.secret, settings.org_encryption_key.get_secret_value()) if body.secret else None
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


@router.post("/connections/call", response_model=ConnectionCallResult)
async def call_connection(
    body: ConnectionCall,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionCallResult:
    """Perform an outbound call through a saved connection on behalf of a form
    ``call_connection`` button. Any org member may call it (views are member-facing),
    but it is bounded EXACTLY like a workflow ``http_request``: the target host must
    pass the same SSRF allow-list (``WORKFLOW_WEBHOOK_ALLOWLIST`` /
    ``WORKFLOW_TRUSTED_LOCAL_HOSTS``), and the connection's secret is injected
    server-side and never exposed to the browser."""
    from urllib.parse import urlsplit

    import httpx

    from api.services.crypto import decrypt_secret
    from api.services.workflow.actions import (
        ActionError,
        ResolvedConnection,
        _auth_headers,
        assert_outbound_host_allowed,
    )

    conn = await WorkflowConnectionRepository(session, ctx.org_id).get_by_name(body.connection)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"connection not found: {body.connection!r}")
    org_key = settings.org_encryption_key.get_secret_value()
    resolved = ResolvedConnection(
        name=conn.name,
        base_url=conn.base_url,
        auth_type=conn.auth_type,
        secret=decrypt_secret(conn.secret_encrypted, org_key) if conn.secret_encrypted else None,
        config=conn.config or {},
    )

    base = (resolved.base_url or "").rstrip("/")
    path = body.path or ""
    url = path if path.startswith(("http://", "https://")) else f"{base}/{path.lstrip('/')}"
    parsed = urlsplit(url)
    try:
        assert_outbound_host_allowed(
            parsed.hostname or "",
            parsed.scheme,
            webhook_allowlist=tuple(settings.workflow_webhook_allowlist or ()),
            trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
            action="call_connection",
        )
    except ActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    headers = {"Content-Type": "application/json", **_auth_headers(resolved)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                body.method,
                url,
                json=body.body if body.method != "GET" else None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"connection call failed: {exc}") from exc

    try:
        payload: Any = resp.json()
    except ValueError:
        payload = resp.text
    return ConnectionCallResult(ok=resp.is_success, status_code=resp.status_code, body=payload)


# --------------------------------------------------------------------------- #
# Inbound webhook endpoints (org-admin) — public URLs that start a workflow run
# --------------------------------------------------------------------------- #
@router.get("/inbound-endpoints", response_model=list[InboundEndpointRead])
async def list_inbound_endpoints(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[InboundEndpointRead]:
    items = await WorkflowInboundEndpointRepository(session, ctx.org_id).list_all()

    def _read(e: object) -> InboundEndpointRead:
        r = InboundEndpointRead.model_validate(e)
        r.has_signing_secret = bool(getattr(e, "signing_secret_encrypted", None))
        return r

    return [_read(e) for e in items]


@router.post("/inbound-endpoints", response_model=InboundEndpointCreated, status_code=status.HTTP_201_CREATED)
async def create_inbound_endpoint(
    body: InboundEndpointCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InboundEndpointCreated:
    import secrets

    from api.services.crypto import encrypt_secret
    from api.services.workflow.inbound import hash_token
    from api.services.workflow.webhook_signing import SIGNATURE_HEADER

    wf = await WorkflowRepository(session, ctx.org_id).get(body.workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
    token = secrets.token_urlsafe(32)
    # The signing secret authenticates each POST (HMAC), so a leaked URL alone
    # can't trigger the workflow. Stored Fernet-encrypted; shown to the creator once.
    signing_secret = "whsec_" + secrets.token_urlsafe(32)
    endpoint = await WorkflowInboundEndpointRepository(session, ctx.org_id).create(
        name=body.name,
        workflow_id=body.workflow_id,
        token_hash=hash_token(token),
        signing_secret_encrypted=encrypt_secret(signing_secret, settings.org_encryption_key.get_secret_value()),
    )
    # Token + secret are shown ONCE (only a hash / ciphertext is stored). Build the URL.
    url = f"{settings.public_base_url.rstrip('/')}/api/inbound/{token}"
    read = InboundEndpointCreated.model_validate(endpoint)
    read.token = token
    read.url = url
    read.signing_secret = signing_secret
    read.signature_header = SIGNATURE_HEADER
    read.has_signing_secret = True
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
        run_inline_on_change=body.run_inline_on_change,
        run_permission=body.run_permission.model_dump(mode="json") if body.run_permission is not None else None,
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
    version = await resolve_published_version(session, ctx.org_id, wf)

    return await execute_workflow_run(
        session,
        ctx.org_id,
        wf,
        version,
        request=body,
        actor_user_id=ctx.user.profile_id,
        settings=settings,
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
            version_id, operation=body.operation, before=body.before, after=body.after, inputs=body.inputs
        )
    except WorkflowNotFoundError as exc:
        _raise(exc)
    return WorkflowTestResult.model_validate(result)


@router.get("/runs/recent", response_model=list[WorkflowRunActivityRead])
async def list_recent_runs(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[WorkflowRunActivityRead]:
    """Most-recent runs across all workflows in the org — the activity feed on
    the workflows page. Declared before ``/{workflow_id}/...`` so the literal
    ``runs/recent`` path is never shadowed by the workflow-id routes."""
    rows = await WorkflowService(session, ctx.org_id).recent_runs(limit=limit)
    return [
        WorkflowRunActivityRead(**WorkflowRunRead.model_validate(run).model_dump(), workflow_name=name)
        for run, name in rows
    ]


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


@router.post("/runs/{run_id}/complete-task", response_model=CompleteTaskResult)
async def complete_run_task(
    run_id: uuid.UUID,
    body: CompleteTaskRequest,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CompleteTaskResult:
    """Complete a human task a run is waiting on (the user-task inbox action).

    Reactivates the parked wait token, merging any decision ``variables`` the flow
    branches on, then advances the run. Any org member may act (the assignment
    model is a future refinement).
    """
    from api.repositories.workflow import WorkflowRunRepository
    from api.services.workflow.engine import TokenEngine

    run_repo = WorkflowRunRepository(session, ctx.org_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    if run.status not in ("waiting", "running"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not awaiting a task")
    version = await WorkflowVersionRepository(session, ctx.org_id).get(run.workflow_version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workflow version missing")
    engine = TokenEngine(
        session,
        webhook_allowlist=tuple(settings.workflow_webhook_allowlist or ()),
        trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
        public_base_url=settings.public_base_url,
        email_sender=EmailSender(settings),
        org_encryption_key=settings.org_encryption_key.get_secret_value(),
        settings=settings,
    )
    signaled = await engine.signal_token(
        run, node_id=body.node_id, variables=body.variables or None, output=body.output or None
    )
    if not signaled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no human task is waiting on this run")
    await engine.drive_run(run)
    refreshed = await run_repo.get_by_id(run_id)
    return CompleteTaskResult(run_id=run_id, status=refreshed.status if refreshed is not None else run.status)


# --------------------------------------------------------------------------- #
# Live run visualization — SSE stream of a run's per-node status + token positions
# --------------------------------------------------------------------------- #
_RUN_TERMINAL = ("succeeded", "failed", "skipped")


async def _run_stream_snapshot(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any] | None:
    """A snapshot for the canvas overlay: run status + a node->status map + live
    token positions. ``None`` if the run doesn't exist (or is cross-org)."""
    from api.repositories.workflow import WorkflowRunRepository, WorkflowTokenRepository

    run_repo = WorkflowRunRepository(session, org_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        return None
    steps = await run_repo.steps_for_run(run_id)
    tokens = await WorkflowTokenRepository(session, org_id).list_for_run(run_id)

    # Per-node status: the recorded step status wins (ordered by step_index, last
    # wins); a node holding only a live token (e.g. parked at a gateway/user task
    # before any step) shows running/waiting.
    nodes: dict[str, str] = {}
    for step in steps:
        nodes[step.node_id] = step.status
    for token in tokens:
        if token.status in ("active", "running"):
            nodes.setdefault(token.node_id, "running")
        elif token.status == "waiting":
            nodes.setdefault(token.node_id, "waiting")

    return {
        "run": {"id": str(run.id), "status": run.status, "dead_letter": run.dead_letter, "error": run.error},
        "nodes": nodes,
        "tokens": [
            {"node_id": t.node_id, "status": t.status, "wait_kind": t.wait_kind}
            for t in tokens
            if t.status in ("active", "running", "waiting")
        ],
    }


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Server-Sent Events stream of a run's live state for the designer overlay.

    Poll-to-stream: a fresh short-lived session per tick (never pins a pool
    connection for the stream's lifetime), emitting a ``snapshot`` frame only when
    the state changes and a ``done`` frame when the run reaches a terminal state or
    the cap elapses. The client falls back to ``listRuns`` polling if the stream is
    unavailable.
    """
    org_id = ctx.org_id
    poll_seconds = 1.0
    max_ticks = 900  # ~15 min ceiling per connection

    async def iterator() -> AsyncGenerator[bytes]:
        factory = get_session_factory(settings)
        last_signature: str | None = None
        try:
            for _ in range(max_ticks):
                async with factory() as session:
                    await db_scope.enter_tenant(session, org_id)
                    snapshot = await _run_stream_snapshot(session, org_id, run_id)
                if snapshot is None:
                    yield b'event: error\ndata: {"detail": "run not found"}\n\n'
                    return
                signature = json.dumps(snapshot, sort_keys=True, default=str)
                if signature != last_signature:
                    last_signature = signature
                    yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n".encode()
                if snapshot["run"]["status"] in _RUN_TERMINAL:
                    yield f"event: done\ndata: {json.dumps(snapshot['run'], default=str)}\n\n".encode()
                    return
                await asyncio.sleep(poll_seconds)
            yield b'event: done\ndata: {"timeout": true}\n\n'
        except asyncio.CancelledError:  # client disconnected — end quietly
            raise
        except Exception:  # noqa: BLE001 - never break the SSE frame contract
            yield b'event: error\ndata: {"detail": "stream failed"}\n\n'

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
