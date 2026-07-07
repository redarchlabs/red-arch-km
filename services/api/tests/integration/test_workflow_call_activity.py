"""Integration: call activity — a task starts a CHILD workflow and blocks on it.

Synchronous: the child runs to completion inline and the parent captures its
variables. Async: the child parks on its own human task; completing that task
resumes the child, which signals the parent to continue.
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org_and_entity(admin_session: AsyncSession) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=f"WFC-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing", slug="thing",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    return org


async def _publish(admin_session: AsyncSession, org: Org, definition: dict):
    from api.repositories.custom_entity import EntityDefinitionRepository

    entity = await EntityDefinitionRepository(admin_session, org.id).get_by_slug("thing")
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    wf = await wf_repo.create(name=f"WF-{uuid.uuid4().hex[:6]}", entity_definition_id=entity.id, description=None)
    version = await ver_repo.create(workflow_id=wf.id, version_number=1, definition=definition)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(wf, enabled=True, active_version_id=version.id)
    await admin_session.commit()
    return wf


async def _new_run(admin_session: AsyncSession, org: Org, wf):
    await set_tenant(admin_session, str(org.id))
    version = await WorkflowVersionRepository(admin_session, org.id).get(wf.active_version_id)
    run = await WorkflowRunRepository(admin_session, org.id).create_run_if_absent(
        workflow_id=wf.id, workflow_version_id=version.id, outbox_id=uuid.uuid4(), outbox_seq=None,
        created_at=datetime.now(UTC), trigger_operation="update", record_id=None,
        input_snapshot={"before": None, "after": {}}, depth=0,
    )
    await admin_session.commit()
    return run, version.definition


async def _drive(admin_session: AsyncSession, run, definition):
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()
    return engine


async def test_call_activity_runs_child_and_captures_result(admin_session: AsyncSession) -> None:
    org = await _org_and_entity(admin_session)
    # Child: derive a variable, then end.
    child_wf = await _publish(admin_session, org, {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "calc", "type": "task", "data": {"task_type": "script", "transform": {"answer": 42}}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "calc"}, {"id": "e1", "source": "calc", "target": "e"}],
    })
    # Parent: call the child, capture its variables, then end.
    parent_wf = await _publish(admin_session, org, {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "call", "type": "task",
             "data": {"task_type": "call", "call_workflow_id": str(child_wf.id), "capture": "sub"}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "call"}, {"id": "e1", "source": "call", "target": "e"}],
    })

    run, definition = await _new_run(admin_session, org, parent_wf)
    await _drive(admin_session, run, definition)

    # Parent completed and captured the child's variables.
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "succeeded"
    assert (fresh.variables or {}).get("sub") == {"answer": 42}

    # A child run exists, linked back to the parent.
    from api.models.workflow import WorkflowRun

    children = (
        await admin_session.execute(
            select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id, WorkflowRun.org_id == org.id)
        )
    ).scalars().all()
    assert len(children) == 1
    assert children[0].workflow_id == child_wf.id
    assert children[0].status == "succeeded"
    assert children[0].depth == 1


async def test_call_activity_failed_child_fails_parent(admin_session: AsyncSession) -> None:
    org = await _org_and_entity(admin_session)
    # Child fails (webhook with empty allowlist).
    child_wf = await _publish(admin_session, org, {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "boom", "type": "task",
             "data": {"task_type": "send", "action_type": "send_webhook", "config": {"url": "https://x.example/y"}}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "boom"}, {"id": "e1", "source": "boom", "target": "e"}],
    })
    parent_wf = await _publish(admin_session, org, {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "call", "type": "task", "data": {"task_type": "call", "call_workflow_id": str(child_wf.id)}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "call"}, {"id": "e1", "source": "call", "target": "e"}],
    })
    run, definition = await _new_run(admin_session, org, parent_wf)
    await _drive(admin_session, run, definition)

    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "failed"


async def test_call_activity_resumes_after_child_human_task(admin_session: AsyncSession) -> None:
    """A child with its own user task: the parent parks until the child completes,
    then _signal_parent resumes it."""
    org = await _org_and_entity(admin_session)
    child_wf = await _publish(admin_session, org, {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "approve", "type": "task", "data": {"task_type": "user"}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "approve"}, {"id": "e1", "source": "approve", "target": "e"}],
    })
    parent_wf = await _publish(admin_session, org, {
        "schema_version": 2,
        "nodes": [
            {"id": "s", "type": "trigger", "data": {}},
            {"id": "call", "type": "task", "data": {"task_type": "call", "call_workflow_id": str(child_wf.id)}},
            {"id": "e", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"id": "e0", "source": "s", "target": "call"}, {"id": "e1", "source": "call", "target": "e"}],
    })
    run, definition = await _new_run(admin_session, org, parent_wf)
    engine = await _drive(admin_session, run, definition)

    # Parent is parked waiting on the child (which is parked on its user task).
    parent = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert parent.status == "waiting"
    from api.models.workflow import WorkflowRun

    child = (
        await admin_session.execute(
            select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id, WorkflowRun.org_id == org.id)
        )
    ).scalars().one()
    assert child.status == "waiting"

    # Complete the child's user task → child finishes → parent is signaled.
    child_fresh = await WorkflowRunRepository(admin_session, org.id).get_by_id(child.id)
    await engine.signal_token(child_fresh, node_id="approve")
    await admin_session.commit()
    await engine.drive_run(child_fresh)
    await admin_session.commit()

    # The parent token was reactivated by _signal_parent; drive the parent to finish.
    parent_fresh = await WorkflowRunRepository(admin_session, org.id).get_by_id(run.id)
    await engine.drive_run(parent_fresh)
    await admin_session.commit()

    final = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert final.status == "succeeded"
