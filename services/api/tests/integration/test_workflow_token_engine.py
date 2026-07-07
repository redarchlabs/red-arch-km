"""Integration tests for the BPMN token engine (services/workflow/engine.py).

Exercises the real claim/lease/advisory-lock machinery against Postgres: linear
v2 flow, exclusive routing, parallel fork + AND-join (multiple concurrent
tokens), timer park + resume, run-scoped variables, and the run-step budget that
bounds loops. Runs on the privileged ``admin_session`` like the dispatcher.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from api.models.org import Org
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowTokenRepository,
    WorkflowVersionRepository,
)
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.engine import TokenEngine
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(
    admin_session: AsyncSession, definition: dict, *, snapshot: dict | None = None
) -> tuple[Org, object, object]:
    """Create org + minimal entity + published workflow version + a run row."""
    await set_tenant(admin_session, None)
    org = Org(name=f"WFT-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing",
            slug="thing",
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
        input_snapshot=snapshot or {"before": None, "after": {}},
        depth=0,
    )
    await admin_session.commit()
    return org, workflow, run


def _task(node_id: str, message: str) -> dict:
    return {"id": node_id, "type": "task", "data": {"task_type": "service", "action_type": "log", "config": {"message": message}}}


async def _drive(admin_session: AsyncSession, run) -> TokenEngine:
    engine = TokenEngine(admin_session)
    await engine.start_run(run, (await _version_def(admin_session, run)))
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()
    return engine


async def _version_def(admin_session: AsyncSession, run) -> dict:
    version = await WorkflowVersionRepository(admin_session, run.org_id).get(run.workflow_version_id)
    return version.definition


async def _reload_run(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).get(run.id, run.created_at)


# --------------------------------------------------------------------------- #
async def test_linear_v2_flow_succeeds(admin_session: AsyncSession, session: AsyncSession) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            _task("t1", "hello"),
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "t1"},
            {"id": "e2", "source": "t1", "target": "end"},
        ],
    }
    org, workflow, run = await _seed(admin_session, definition)
    await _drive(admin_session, run)

    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "succeeded"
    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    assert [s.action_type for s in steps] == ["log"]
    assert steps[0].status == "succeeded"
    assert steps[0].output == {"logged": "hello"}
    assert steps[0].token_id is not None  # step is attributed to its token


async def test_exclusive_gateway_routes_true_branch(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive", "expr": {"==": [{"var": "after.x"}, "yes"]}}},
            _task("yes", "YES"),
            _task("no", "NO"),
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "gw"},
            {"id": "e1", "source": "gw", "target": "yes", "source_handle": "true"},
            {"id": "e2", "source": "gw", "target": "no", "source_handle": "false"},
            {"id": "e3", "source": "yes", "target": "end"},
        ],
    }
    org, workflow, run = await _seed(admin_session, definition, snapshot={"before": None, "after": {"x": "yes"}})
    await _drive(admin_session, run)

    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "succeeded"
    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    # Only the true branch ran.
    assert [s.output.get("logged") for s in steps] == ["YES"]


async def test_parallel_fork_and_join(admin_session: AsyncSession, session: AsyncSession) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "fork", "type": "gateway", "data": {"gateway_type": "parallel"}},
            _task("a", "A"),
            _task("b", "B"),
            {"id": "join", "type": "gateway", "data": {"gateway_type": "parallel"}},
            _task("after", "AFTER"),
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "fork"},
            {"id": "e1", "source": "fork", "target": "a"},
            {"id": "e2", "source": "fork", "target": "b"},
            {"id": "e3", "source": "a", "target": "join"},
            {"id": "e4", "source": "b", "target": "join"},
            {"id": "e5", "source": "join", "target": "after"},
            {"id": "e6", "source": "after", "target": "end"},
        ],
    }
    org, workflow, run = await _seed(admin_session, definition)
    await _drive(admin_session, run)

    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "succeeded"
    logs = {
        s.output.get("logged")
        for s in await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    }
    # Both parallel branches ran, and the post-join task ran exactly once.
    assert logs == {"A", "B", "AFTER"}
    # The join fired exactly once → exactly one token reached 'after'/'end'.
    tokens = await WorkflowTokenRepository(admin_session, org.id).list_for_run(run.id)
    assert all(t.status in ("completed", "dead") for t in tokens)


async def test_timer_event_parks_then_resumes(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "wait", "type": "event", "data": {"position": "intermediate", "event_type": "timer", "throw_catch": "catch", "delay_seconds": 0}},
            _task("t1", "done-waiting"),
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "wait"},
            {"id": "e1", "source": "wait", "target": "t1"},
            {"id": "e2", "source": "t1", "target": "end"},
        ],
    }
    org, workflow, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    # Parked at the timer → run is waiting, no task step yet.
    parked = await _reload_run(admin_session, run)
    assert parked.status == "waiting"
    assert await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id) == []

    # Timer sweep reactivates the due token; then it runs to completion.
    await set_tenant(admin_session, str(org.id))
    reactivated = await engine.resume_due_tokens()
    await admin_session.commit()
    assert reactivated["reactivated"] == 1
    await engine.drive_run(run)
    await admin_session.commit()

    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "succeeded"
    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    assert [s.output.get("logged") for s in steps] == ["done-waiting"]


async def test_gateway_routes_on_run_variables(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive", "expr": {"var": "vars.approved"}}},
            _task("yes", "APPROVED"),
            _task("no", "DENIED"),
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "gw"},
            {"id": "e1", "source": "gw", "target": "yes", "source_handle": "true"},
            {"id": "e2", "source": "gw", "target": "no", "source_handle": "false"},
        ],
    }
    org, workflow, run = await _seed(admin_session, definition)
    # Preset a run variable, then drive.
    await set_tenant(admin_session, str(org.id))
    await WorkflowRunRepository(admin_session, org.id).set_variables(run, {"approved": True})
    await admin_session.commit()
    run = await _reload_run(admin_session, run)

    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    assert [s.output.get("logged") for s in steps] == ["APPROVED"]


async def test_infinite_loop_is_bounded_and_fails(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    """A conditionless self-loop must terminate (as failed) via the step budget,
    never hang."""
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            _task("loop", "spin"),
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "loop"},
            {"id": "e1", "source": "loop", "target": "loop"},
        ],
    }
    org, workflow, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run, max_passes=5000)
    await admin_session.commit()

    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "failed"
    assert "max run steps" in (fresh.error or "")
