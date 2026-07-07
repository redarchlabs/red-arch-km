"""Integration: the reachability (dead-path-aware) inclusive OR-join.

An exclusive split feeds a converging join: only one incoming edge ever carries a
token. The OR-join fires with that one token (no other live token can reach it),
whereas an AND-join would deadlock waiting for the branch that never ran.
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
    org = Org(name=f"WFJ-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing", slug="thing",
            fields=[EntityFieldCreate(name="Flag", slug="flag", field_type="boolean")],
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
        input_snapshot={"before": None, "after": {"flag": True}},
        depth=0,
    )
    await admin_session.commit()
    return org, workflow, run


def _log(node_id: str, message: str) -> dict:
    return {"id": node_id, "type": "task",
            "data": {"task_type": "service", "action_type": "log", "config": {"message": message}}}


def _split_join_graph(join_type: str) -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "split", "type": "gateway",
             "data": {"gateway_type": "exclusive", "expr": {"var": "after.flag"}}},
            _log("a", "A"),
            _log("b", "B"),
            {"id": "join", "type": "gateway", "data": {"gateway_type": join_type}},
            _log("after", "joined"),
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": "split"},
            {"id": "e1", "source": "split", "target": "a", "source_handle": "true"},
            {"id": "e2", "source": "split", "target": "b", "source_handle": "false"},
            {"id": "e3", "source": "a", "target": "join"},
            {"id": "e4", "source": "b", "target": "join"},
            {"id": "e5", "source": "join", "target": "after"},
            {"id": "e6", "source": "after", "target": "end"},
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


async def test_inclusive_join_fires_after_exclusive_split(admin_session: AsyncSession) -> None:
    definition = _split_join_graph("inclusive")
    org, _wf, run = await _seed(admin_session, definition)
    await _drive(admin_session, run, definition)

    steps = {s.node_id for s in await _steps(admin_session, run)}
    assert "a" in steps and "b" not in steps   # exclusive picked the true branch
    assert "after" in steps                     # the OR-join fired with the single token
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "succeeded"


async def test_parallel_join_deadlocks_on_the_same_graph(admin_session: AsyncSession) -> None:
    """Contrast: an AND-join on the identical split waits forever for the branch
    that never ran, so the run never reaches the node after the join."""
    definition = _split_join_graph("parallel")
    org, _wf, run = await _seed(admin_session, definition)
    await _drive(admin_session, run, definition)

    steps = {s.node_id for s in await _steps(admin_session, run)}
    assert "a" in steps and "after" not in steps  # blocked at the AND-join
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "waiting"              # parked at the join, not completed
