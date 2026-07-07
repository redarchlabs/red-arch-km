"""Integration: the run-stream snapshot that feeds the live canvas overlay.

Covers _run_stream_snapshot (per-node status map + live token positions) — the
data the SSE endpoint emits. The step status wins for a node; a node holding only
a live token (parked) shows waiting/running.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from api.models.org import Org
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.routers.workflows import _run_stream_snapshot
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.engine import TokenEngine
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession, definition: dict):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFSTR-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing", slug="thing",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    wf = await wf_repo.create(name="WF", entity_definition_id=entity.id, description=None)
    version = await ver_repo.create(workflow_id=wf.id, version_number=1, definition=definition)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(wf, enabled=True, active_version_id=version.id)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    run = await WorkflowRunRepository(admin_session, org.id).create_run_if_absent(
        workflow_id=wf.id, workflow_version_id=version.id, outbox_id=uuid.uuid4(), outbox_seq=None,
        created_at=datetime.now(UTC), trigger_operation="update", record_id=None,
        input_snapshot={"before": None, "after": {}}, depth=0,
    )
    await admin_session.commit()
    return org, run


async def test_snapshot_reports_node_status_and_parked_token(admin_session: AsyncSession) -> None:
    # trigger -> log (records a succeeded step) -> user task (parks) -> end.
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "log1", "type": "task",
             "data": {"task_type": "service", "action_type": "log", "config": {"message": "hi"}}},
            {"id": "approve", "type": "task", "data": {"task_type": "user"}},
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "log1"},
            {"id": "e1", "source": "log1", "target": "approve"},
            {"id": "e2", "source": "approve", "target": "end"},
        ],
    }
    org, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    snapshot = await _run_stream_snapshot(admin_session, org.id, run.id)
    assert snapshot is not None
    assert snapshot["run"]["status"] == "waiting"
    assert snapshot["run"]["dead_letter"] is False
    # log1 completed (step status), approve is parked (live token).
    assert snapshot["nodes"]["log1"] == "succeeded"
    assert snapshot["nodes"]["approve"] == "waiting"
    parked = [t for t in snapshot["tokens"] if t["node_id"] == "approve"]
    assert len(parked) == 1 and parked[0]["wait_kind"] == "user_task"


async def test_snapshot_terminal_run_has_no_live_tokens(admin_session: AsyncSession) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "log1", "type": "task",
             "data": {"task_type": "service", "action_type": "log", "config": {"message": "done"}}},
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "start", "target": "log1"}, {"id": "e1", "source": "log1", "target": "end"}],
    }
    org, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    snapshot = await _run_stream_snapshot(admin_session, org.id, run.id)
    assert snapshot is not None
    assert snapshot["run"]["status"] == "succeeded"
    assert snapshot["nodes"]["log1"] == "succeeded"
    assert snapshot["tokens"] == []  # nothing live once the run finished


async def test_snapshot_unknown_run_is_none(admin_session: AsyncSession) -> None:
    org, _run = await _seed(admin_session, {"schema_version": 2, "nodes": [], "edges": []})
    assert await _run_stream_snapshot(admin_session, org.id, uuid.uuid4()) is None
