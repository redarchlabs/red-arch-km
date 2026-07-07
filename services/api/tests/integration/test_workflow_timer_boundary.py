"""Integration: timer/escalation boundary on a wait task.

A user task with an attached timer boundary parks with an SLA deadline. If the
timer fires first the token routes to the (interrupting) escalation path; if the
task is completed first, completion wins and the timer is moot.
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


def _log(node_id: str, message: str) -> dict:
    return {"id": node_id, "type": "task",
            "data": {"task_type": "service", "action_type": "log", "config": {"message": message}}}


def _definition(delay: int = 0) -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "approve", "type": "task", "data": {"task_type": "user"}},
            {"id": "sla", "type": "event",
             "data": {"position": "boundary", "event_type": "timer", "attached_to": "approve", "delay_seconds": delay}},
            _log("done", "approved"),
            _log("escalated", "escalated"),
            {"id": "e1", "type": "event", "data": {"position": "end", "event_type": "none"}},
            {"id": "e2", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "x0", "source": "start", "target": "approve"},
            {"id": "x1", "source": "approve", "target": "done"},       # completion path
            {"id": "x2", "source": "sla", "target": "escalated"},       # timer/escalation path
            {"id": "x3", "source": "done", "target": "e1"},
            {"id": "x4", "source": "escalated", "target": "e2"},
        ],
    }


async def _seed(admin_session: AsyncSession, definition: dict):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFTB-{uuid.uuid4().hex[:8]}")
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


async def _steps(admin_session: AsyncSession, run):
    return {s.node_id: s for s in await WorkflowRunRepository(admin_session, run.org_id).steps_for_run(run.id)}


async def test_timer_boundary_fires_escalation_on_timeout(admin_session: AsyncSession) -> None:
    definition = _definition(delay=0)
    org, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()

    # Parks at the user task, armed with an (immediate) SLA deadline.
    await engine.drive_run(run)
    await admin_session.commit()
    assert (await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)).status == "waiting"

    # The timer sweep reactivates the armed token; re-driving takes the escalation.
    assert (await engine.resume_due_tokens())["reactivated"] == 1
    await admin_session.commit()
    admin_session.expunge_all()  # drop stale identity-map token state before re-drive
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    await engine.drive_run(fresh)
    await admin_session.commit()

    steps = await _steps(admin_session, run)
    assert "escalated" in steps and steps["escalated"].status == "succeeded"
    assert "done" not in steps  # completion path NOT taken
    assert steps["approve"].status == "skipped"  # the task timed out
    final = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert final.status == "succeeded"


async def test_completion_beats_timer_boundary(admin_session: AsyncSession) -> None:
    # A long deadline so the timer never fires within the test.
    definition = _definition(delay=3600)
    org, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert await engine.signal_token(fresh, node_id="approve") is True
    await admin_session.commit()
    await engine.drive_run(fresh)
    await admin_session.commit()

    steps = await _steps(admin_session, run)
    assert "done" in steps and steps["done"].status == "succeeded"  # completion path
    assert "escalated" not in steps
    assert steps["approve"].status == "succeeded"
    final = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert final.status == "succeeded"
