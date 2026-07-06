"""HTTP-level unit tests for the workflows router run endpoint.

The repository/dispatcher layers are mocked, so no database is required. These
cover the access + state gating (403/404/409) and the manual-run trust boundary
(cross-entity record_id → 404; side-effecting action on free-form data → 400).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_access
from api.config import get_settings
from api.dependencies import get_db
from api.routers import workflows
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
        public_base_url="http://x",
        smtp_host="",
        smtp_from="",
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/workflows")
    app.dependency_overrides[require_org_access] = _ctx
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
        patch.object(workflows, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        patch.object(workflows, "EmailSender", return_value=MagicMock()),
        patch.object(workflows, "WorkflowDispatchService", return_value=disp),
    ):
        async with _client(_app()) as client:
            resp = await client.post(
                f"/api/workflows/{uuid.uuid4()}/run", json={"record_id": str(uuid.uuid4())}
            )
    assert resp.status_code == 404
    disp.load_trigger_record.assert_awaited_once()


async def test_run_400_side_effecting_action_on_freeform_data() -> None:
    wf = SimpleNamespace(
        active_version_id=uuid.uuid4(), run_permission={"mode": "any_member"},
        entity_definition_id=uuid.uuid4(),
    )
    version = _published_version(
        [{"id": "a", "type": "action", "data": {"action_type": "send_email", "config": {}}}]
    )
    disp = MagicMock()
    disp.run_version_manually = AsyncMock()
    with (
        patch.object(workflows, "WorkflowRepository", return_value=_wf_repo(wf)),
        patch.object(workflows, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        patch.object(workflows, "EmailSender", return_value=MagicMock()),
        patch.object(workflows, "WorkflowDispatchService", return_value=disp),
    ):
        async with _client(_app()) as client:
            # No record_id + a send_email action → refuse (free-form data).
            resp = await client.post(f"/api/workflows/{uuid.uuid4()}/run", json={"after": {"x": 1}})
    assert resp.status_code == 400
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
        patch.object(workflows, "WorkflowVersionRepository", return_value=_wf_repo(version)),
        patch.object(workflows, "can_run", return_value=True),
        patch.object(workflows, "EmailSender", return_value=MagicMock()),
        patch.object(workflows, "WorkflowDispatchService", return_value=disp),
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
