"""Integration tests for BPMN error handling: error boundary events + error-end.

An error boundary attached to a task turns a task failure into a try/catch: the
token is routed to the boundary's error path (the run continues / succeeds)
instead of failing. An error END event throws — in a flat graph that fails the
run. Uses ``send_webhook`` + empty allowlist as a deterministic, network-free
task failure (rejected at the SSRF guard).
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
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.engine import TokenEngine
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession, definition: dict):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFE-{uuid.uuid4().hex[:8]}")
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
    workflow = await wf_repo.create(name="WF", entity_definition_id=entity.id, description=None)
    version = await ver_repo.create(workflow_id=workflow.id, version_number=1, definition=definition)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(workflow, enabled=True, active_version_id=version.id)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    run = await WorkflowRunRepository(admin_session, org.id).create_run_if_absent(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=datetime.now(UTC),
        trigger_operation="update",
        record_id=None,
        input_snapshot={"before": None, "after": {}},
        depth=0,
    )
    await admin_session.commit()
    return org, workflow, run


async def _run_to_quiescence(admin_session: AsyncSession, run, definition):
    engine = TokenEngine(admin_session)  # empty allowlist ⇒ webhook fails
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()


def _log_task(node_id: str, message: str) -> dict:
    return {
        "id": node_id,
        "type": "task",
        "data": {"task_type": "service", "action_type": "log", "config": {"message": message}},
    }


async def _reload_run(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).get(run.id, run.created_at)


async def _steps(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).steps_for_run(run.id)


async def test_error_boundary_catches_task_failure(admin_session: AsyncSession) -> None:
    """A task with an attached error boundary: the failure is caught and the run
    follows the error path to completion (succeeds, not dead-lettered)."""
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {
                "id": "call",
                "type": "task",
                "data": {"task_type": "send", "action_type": "send_webhook", "config": {"url": "https://x.example/y"}},
            },
            {
                "id": "onerr",
                "type": "event",
                "data": {"position": "boundary", "event_type": "error", "attached_to": "call"},
            },
            _log_task("recover", "recovered"),
            {"id": "end_ok", "type": "event", "data": {"position": "end", "event_type": "none"}},
            {"id": "end_err", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "call"},
            {"id": "e1", "source": "call", "target": "end_ok"},        # normal path (task succeeds) — not taken
            {"id": "e2", "source": "onerr", "target": "recover"},       # error path
            {"id": "e3", "source": "recover", "target": "end_err"},
        ],
    }
    org, _wf, run = await _seed(admin_session, definition)
    await _run_to_quiescence(admin_session, run, definition)

    steps = {s.node_id: s for s in await _steps(admin_session, run)}
    assert steps["call"].status == "failed"          # the task really failed
    assert steps["recover"].status == "succeeded"    # ...but the error path ran
    assert steps["recover"].output == {"logged": "recovered"}
    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "succeeded"               # a caught error does NOT fail the run
    assert fresh.dead_letter is False                # ...nor dead-letter it


async def test_uncaught_task_failure_dead_letters(admin_session: AsyncSession) -> None:
    """Same failing task with NO boundary → the run fails and is dead-lettered."""
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {
                "id": "call",
                "type": "task",
                "data": {"task_type": "send", "action_type": "send_webhook", "config": {"url": "https://x.example/y"}},
            },
            {"id": "end_ok", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "call"},
            {"id": "e1", "source": "call", "target": "end_ok"},
        ],
    }
    org, _wf, run = await _seed(admin_session, definition)
    await _run_to_quiescence(admin_session, run, definition)

    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "failed"
    assert fresh.dead_letter is True


async def test_error_end_event_fails_the_run(admin_session: AsyncSession) -> None:
    """Reaching an error END event throws: in a flat graph the run fails."""
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            _log_task("t", "before-throw"),
            {"id": "boom", "type": "event", "data": {"position": "end", "event_type": "error", "error_code": "BOOM"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "t"},
            {"id": "e1", "source": "t", "target": "boom"},
        ],
    }
    org, _wf, run = await _seed(admin_session, definition)
    await _run_to_quiescence(admin_session, run, definition)

    steps = await _steps(admin_session, run)
    assert steps[0].node_id == "t" and steps[0].status == "succeeded"  # ran up to the throw
    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "failed"
    assert "BOOM" in (fresh.error or "")
