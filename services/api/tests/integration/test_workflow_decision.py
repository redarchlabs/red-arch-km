"""Integration: a businessRule (decision-table) task derives a run variable that a
downstream exclusive gateway routes on — the data-derivation-then-branch pattern."""

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
    org = Org(name=f"WFD-{uuid.uuid4().hex[:8]}")
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
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=datetime.now(UTC),
        trigger_operation="update",
        record_id=None,
        input_snapshot=snapshot,
        depth=0,
    )
    await admin_session.commit()
    return org, workflow, run


def _definition() -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {
                "id": "decide",
                "type": "task",
                "data": {
                    "task_type": "businessRule",
                    "decision_table": {
                        "hit_policy": "first",
                        "rules": [
                            {"when": {">=": [{"var": "after.amount"}, 100]}, "output": {"tier": "gold"}},
                            {"output": {"tier": "standard"}},
                        ],
                    },
                },
            },
            {
                "id": "gw",
                "type": "gateway",
                "data": {"gateway_type": "exclusive", "expr": {"==": [{"var": "vars.tier"}, "gold"]}},
            },
            {"id": "gold", "type": "task",
             "data": {"task_type": "service", "action_type": "log", "config": {"message": "vip"}}},
            {"id": "std", "type": "task",
             "data": {"task_type": "service", "action_type": "log", "config": {"message": "normal"}}},
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "decide"},
            {"id": "e1", "source": "decide", "target": "gw"},
            {"id": "e2", "source": "gw", "target": "gold", "source_handle": "true"},
            {"id": "e3", "source": "gw", "target": "std", "source_handle": "false"},
            {"id": "e4", "source": "gold", "target": "end"},
            {"id": "e5", "source": "std", "target": "end"},
        ],
    }


async def _drive(admin_session: AsyncSession, run, definition):
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()


async def _steps(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).steps_for_run(run.id)


async def test_decision_table_derives_variable_and_gateway_routes_gold(admin_session: AsyncSession) -> None:
    definition = _definition()
    org, _wf, run = await _seed(admin_session, definition, {"before": None, "after": {"amount": 150}})
    await _drive(admin_session, run, definition)

    steps = {s.node_id: s for s in await _steps(admin_session, run)}
    assert steps["decide"].output == {"tier": "gold"}      # table derived the tier
    assert "gold" in steps and steps["gold"].status == "succeeded"  # routed to the VIP path
    assert "std" not in steps                               # the other branch did not run
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "succeeded"
    assert (fresh.variables or {}).get("tier") == "gold"    # published as a run variable


async def test_decision_table_default_row_routes_standard(admin_session: AsyncSession) -> None:
    definition = _definition()
    org, _wf, run = await _seed(admin_session, definition, {"before": None, "after": {"amount": 20}})
    await _drive(admin_session, run, definition)

    steps = {s.node_id: s for s in await _steps(admin_session, run)}
    assert steps["decide"].output == {"tier": "standard"}
    assert "std" in steps and steps["std"].status == "succeeded"
    assert "gold" not in steps
