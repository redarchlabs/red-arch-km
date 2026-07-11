"""HTTP-level unit tests for the workflows router run endpoint.

The repository/dispatcher layers are mocked, so no database is required. These
cover the access + state gating (403/404/409) and the manual-run trust boundary
(cross-entity record_id → 404; a no-record side-effecting run drops client
before/after so only author-defined config leaves the org).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import get_settings
from api.dependencies import get_db
from api.routers import workflows
from api.services.workflow import manual_run
from fastapi import FastAPI

pytestmark = pytest.mark.unit


def _ctx() -> OrgContext:
    return OrgContext(
        user=SimpleNamespace(profile_id=uuid.uuid4()),
        org_id=uuid.uuid4(),
        membership=MagicMock(),
        is_org_admin=False,
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        workflow_webhook_allowlist=(),
        workflow_trusted_local_hosts=(),
        public_base_url="http://x",
        smtp_host="",
        smtp_from="",
        org_encryption_key=SimpleNamespace(get_secret_value=lambda: "test-key"),
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/workflows")
    app.dependency_overrides[require_org_access] = _ctx
    app.dependency_overrides[require_org_admin] = _ctx
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_settings] = _settings
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _wf_repo(workflow: object | None) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=workflow)
    return repo


def _published_version(nodes: list[dict]) -> MagicMock:
    v = MagicMock()
    v.status = "published"
    v.definition = {"nodes": nodes, "edges": []}
    return v


def _activity_run() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        workflow_version_id=uuid.uuid4(),
        trigger_operation="update",
        record_id=None,
        status="succeeded",
        conditions_matched=True,
        error=None,
        dead_letter=False,
        depth=0,
        started_at=now,
        finished_at=now,
        created_at=now,
    )


async def test_recent_runs_returns_activity_rows_with_workflow_name() -> None:
    """The activity feed pairs each run with its workflow name and passes the
    limit through to the service."""
    run = _activity_run()
    service = MagicMock()
    service.recent_runs = AsyncMock(return_value=[(run, "Send Intake Form Link")])
    with patch.object(workflows, "WorkflowService", return_value=service):
        async with _client(_app()) as client:
            resp = await client.get("/api/workflows/runs/recent", params={"limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["workflow_name"] == "Send Intake Form Link"
    assert body[0]["workflow_id"] == str(run.workflow_id)
    assert body[0]["status"] == "succeeded"
    service.recent_runs.assert_awaited_once_with(limit=10)


async def test_recent_runs_rejects_out_of_range_limit() -> None:
    async with _client(_app()) as client:
        resp = await client.get("/api/workflows/runs/recent", params={"limit": 0})
    assert resp.status_code == 422


async def test_run_404_when_workflow_missing() -> None:
    with patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(None)):
        async with _client(_app()) as client:
            resp = await client.post(f"/api/workflows/{uuid.uuid4()}/run", json={})
    assert resp.status_code == 404


async def test_run_403_when_cannot_run() -> None:
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "org_admin"},
        entity_definition_id=uuid.uuid4(),
    )
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(workflows, "can_run", return_value=False),
    ):
        async with _client(_app()) as client:
            resp = await client.post(f"/api/workflows/{uuid.uuid4()}/run", json={})
    assert resp.status_code == 403


async def test_run_409_when_no_published_version() -> None:
    wf = SimpleNamespace(
        active_version_id=None, run_permission={"mode": "any_member"},
        entity_definition_id=uuid.uuid4(),
    )
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(workflows, "can_run", return_value=True),
    ):
        async with _client(_app()) as client:
            resp = await client.post(f"/api/workflows/{uuid.uuid4()}/run", json={})
    assert resp.status_code == 409


async def test_run_404_when_cross_entity_or_cross_org_record_id() -> None:
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "any_member"},
        entity_definition_id=uuid.uuid4(),
    )
    version = _published_version(
        [{"id": "a", "type": "action", "data": {"action_type": "log", "config": {}}}]
    )
    disp = MagicMock()
    disp.load_trigger_record = AsyncMock(return_value=None)  # id not in this entity/org
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(manual_run, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        # The dispatcher is now built inside execute_workflow_run (manual_run.py).
        patch.object(manual_run, "build_dispatch_service", return_value=disp),
    ):
        async with _client(_app()) as client:
            resp = await client.post(
                f"/api/workflows/{uuid.uuid4()}/run", json={"record_id": str(uuid.uuid4())}
            )
    assert resp.status_code == 404
    disp.load_trigger_record.assert_awaited_once()


async def test_run_side_effecting_without_record_nulls_client_data() -> None:
    """A no-record run of a side-effecting workflow (e.g. a view 'run now' button)
    is ALLOWED, but the client's before/after are DROPPED — actions execute on
    their own author-defined config only, so no fabricated data leaves the org."""
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "any_member"},
        entity_definition_id=uuid.uuid4(),
    )
    version = _published_version(
        [{"id": "a", "type": "action", "data": {"action_type": "send_webhook", "config": {"url": "https://x"}}}]
    )
    run = SimpleNamespace(id=uuid.uuid4(), status="succeeded", conditions_matched=True, error=None)
    disp = MagicMock()
    disp.run_version_manually = AsyncMock(return_value=(run, 1))
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(manual_run, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        # The dispatcher is now built inside execute_workflow_run (manual_run.py).
        patch.object(manual_run, "build_dispatch_service", return_value=disp),
    ):
        async with _client(_app()) as client:
            # No record_id + a send_webhook action + fabricated `after` → runs,
            # but the fabricated data is discarded.
            resp = await client.post(
                f"/api/workflows/{uuid.uuid4()}/run", json={"after": {"x": "FABRICATED"}}
            )
    assert resp.status_code == 200
    _args, kwargs = disp.run_version_manually.call_args
    assert kwargs["before"] is None and kwargs["after"] is None  # client data dropped


async def test_run_manual_trigger_uses_inputs_even_when_entity_bound() -> None:
    """An entity-bound workflow whose trigger is switched to a manual (on-demand)
    start runs with the caller's inputs — record fields are ignored, inputs coerced
    against the declared schema (undeclared keys dropped)."""
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "any_member"},
        entity_definition_id=uuid.uuid4(),  # still bound to an entity...
    )
    version = _published_version(
        [
            {
                "id": "t",
                "type": "trigger",
                "data": {"source": "manual", "inputs": [{"key": "email", "type": "text", "required": True}]},
            },
            {"id": "a", "type": "action", "data": {"action_type": "send_email", "config": {}}},
        ]
    )
    run = SimpleNamespace(id=uuid.uuid4(), status="succeeded", conditions_matched=True, error=None)
    disp = MagicMock()
    disp.run_version_manually = AsyncMock(return_value=(run, 1))
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(manual_run, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        # The dispatcher is now built inside execute_workflow_run (manual_run.py).
        patch.object(manual_run, "build_dispatch_service", return_value=disp),
    ):
        async with _client(_app()) as client:
            resp = await client.post(
                f"/api/workflows/{uuid.uuid4()}/run",
                json={"inputs": {"email": "a@b.com", "evil": "smuggled"}, "record_id": str(uuid.uuid4())},
            )
    assert resp.status_code == 200
    _args, kwargs = disp.run_version_manually.call_args
    assert kwargs["operation"] == "manual"
    assert kwargs["record_id"] is None
    assert kwargs["inputs"] == {"email": "a@b.com"}  # undeclared key dropped


async def test_run_manual_missing_required_input_422() -> None:
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "any_member"},
        entity_definition_id=None,
    )
    version = _published_version(
        [{"id": "t", "type": "trigger",
          "data": {"source": "manual", "inputs": [{"key": "email", "type": "text", "required": True}]}}]
    )
    disp = MagicMock()
    disp.run_version_manually = AsyncMock()
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(manual_run, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        # The dispatcher is now built inside execute_workflow_run (manual_run.py).
        patch.object(manual_run, "build_dispatch_service", return_value=disp),
    ):
        async with _client(_app()) as client:
            resp = await client.post(f"/api/workflows/{uuid.uuid4()}/run", json={"inputs": {}})
    assert resp.status_code == 422
    disp.run_version_manually.assert_not_awaited()


async def test_run_loads_record_server_side_and_ignores_client_data() -> None:
    """A member cannot mutate a record via fabricated ``after`` — the server
    loads the record and passes THAT as before/after, discarding client data."""
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "any_member"},
        entity_definition_id=uuid.uuid4(),
    )
    version = _published_version(
        [{"id": "a", "type": "action",
          "data": {"action_type": "update_record_field", "config": {"field": "t", "value": "x"}}}]
    )
    loaded = {"id": str(uuid.uuid4()), "title": "REAL"}
    run = SimpleNamespace(id=uuid.uuid4(), status="succeeded", conditions_matched=True, error=None)
    disp = MagicMock()
    disp.load_trigger_record = AsyncMock(return_value=loaded)
    disp.run_version_manually = AsyncMock(return_value=(run, 1))
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(manual_run, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        # The dispatcher is now built inside execute_workflow_run (manual_run.py).
        patch.object(manual_run, "build_dispatch_service", return_value=disp),
    ):
        async with _client(_app()) as client:
            resp = await client.post(
                f"/api/workflows/{uuid.uuid4()}/run",
                json={"record_id": loaded["id"], "after": {"title": "FABRICATED"}},
            )
    assert resp.status_code == 200
    # The dispatcher was called with the server-loaded record, NOT the client's
    # fabricated after.
    _args, kwargs = disp.run_version_manually.call_args
    assert kwargs["before"] == loaded
    assert kwargs["after"] == loaded
