"""Integration: a script/transform task publishes computed run variables that a
downstream gateway routes on."""

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


async def _seed(admin_session: AsyncSession, definition: dict, snapshot: dict):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFS-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Order", slug="order",
            fields=[EntityFieldCreate(name="Amount", slug="amount", field_type="integer")],
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
        workflow_id=workflow.id, workflow_version_id=version.id, outbox_id=uuid.uuid4(),
        outbox_seq=None, created_at=datetime.now(UTC), trigger_operation="update",
        record_id=None, input_snapshot=snapshot, depth=0,
    )
    await admin_session.commit()
    return org, workflow, run


async def test_script_task_computes_variables_and_routes(admin_session: AsyncSession) -> None:
    definition = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "calc", "type": "task", "data": {
                "task_type": "script",
                "transform": {
                    "total": {"*": [{"var": "after.amount"}, 2]},
                    "is_big": {">=": [{"var": "after.amount"}, 100]},
                },
            }},
            {"id": "gw", "type": "gateway",
             "data": {"gateway_type": "exclusive", "expr": {"var": "vars.is_big"}}},
            {"id": "big", "type": "task",
             "data": {"task_type": "service", "action_type": "log", "config": {"message": "big"}}},
            {"id": "small", "type": "task",
             "data": {"task_type": "service", "action_type": "log", "config": {"message": "small"}}},
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "calc"},
            {"id": "e1", "source": "calc", "target": "gw"},
            {"id": "e2", "source": "gw", "target": "big", "source_handle": "true"},
            {"id": "e3", "source": "gw", "target": "small", "source_handle": "false"},
            {"id": "e4", "source": "big", "target": "end"},
            {"id": "e5", "source": "small", "target": "end"},
        ],
    }
    org, _wf, run = await _seed(admin_session, definition, {"before": None, "after": {"amount": 120}})
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    steps = {s.node_id: s for s in await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)}
    assert steps["calc"].output == {"total": 240, "is_big": True}
    assert "big" in steps and "small" not in steps
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "succeeded"
    assert (fresh.variables or {}).get("total") == 240
