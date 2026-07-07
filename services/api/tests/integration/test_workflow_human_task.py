"""Integration: human task (approval) — park on a user task, resume via
signal_token with the decision, and route on it. The primitive behind approvals,
receive/message correlation, and form-submission resume.
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


async def _seed(admin_session: AsyncSession, definition: dict):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFH-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Req", slug="req",
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


def _log(node_id: str, message: str) -> dict:
    return {"id": node_id, "type": "task",
            "data": {"task_type": "service", "action_type": "log", "config": {"message": message}}}


def _approval_definition() -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "approve", "type": "task", "data": {"task_type": "user", "label": "Manager approval"}},
            {"id": "gw", "type": "gateway",
             "data": {"gateway_type": "exclusive", "expr": {"var": "vars.approved"}}},
            _log("do", "provisioned"),
            _log("deny", "rejected"),
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "approve"},
            {"id": "e1", "source": "approve", "target": "gw"},
            {"id": "e2", "source": "gw", "target": "do", "source_handle": "true"},
            {"id": "e3", "source": "gw", "target": "deny", "source_handle": "false"},
            {"id": "e4", "source": "do", "target": "end"},
            {"id": "e5", "source": "deny", "target": "end"},
        ],
    }


async def _steps(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).steps_for_run(run.id)


async def test_user_task_parks_then_completes_and_routes(admin_session: AsyncSession) -> None:
    definition = _approval_definition()
    org, _wf, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()

    # Drive → parks at the user task; run waiting, no steps yet.
    await engine.drive_run(run)
    await admin_session.commit()
    tokens = await WorkflowTokenRepository(admin_session, org.id).list_for_run(run.id)
    parked = [t for t in tokens if t.status == "waiting" and t.wait_kind == "user_task"]
    assert len(parked) == 1 and parked[0].node_id == "approve"
    assert await _steps(admin_session, run) == []
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "waiting"

    # Signal the approval decision → reactivates; drive → completes + routes true.
    signaled = await engine.signal_token(
        fresh, node_id="approve", variables={"approved": True}, output={"decision": "approved"}
    )
    assert signaled is True
    await admin_session.commit()
    await engine.drive_run(fresh)
    await admin_session.commit()

    steps = {s.node_id: s for s in await _steps(admin_session, run)}
    assert steps["approve"].status == "succeeded"
    assert steps["approve"].output == {"decision": "approved"}
    assert "do" in steps and "deny" not in steps       # routed to the approved path
    final = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert final.status == "succeeded"


async def test_signal_returns_false_when_nothing_parked(admin_session: AsyncSession) -> None:
    definition = _approval_definition()
    org, _wf, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    # No tokens yet (run not started) → nothing to signal.
    assert await engine.signal_token(run, node_id="approve") is False


async def test_user_task_rejection_routes_false(admin_session: AsyncSession) -> None:
    definition = _approval_definition()
    org, _wf, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    await engine.signal_token(fresh, node_id="approve", variables={"approved": False})
    await admin_session.commit()
    await engine.drive_run(fresh)
    await admin_session.commit()

    steps = {s.node_id for s in await _steps(admin_session, run)}
    assert "deny" in steps and "do" not in steps
