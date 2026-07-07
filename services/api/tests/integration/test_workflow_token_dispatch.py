"""Integration: dual-engine selection + shadow parity.

Verifies a schema_version-2 workflow runs through the real outbox -> dispatch ->
token engine path, and that a legacy graph produces identical results on the
walker and on the token engine (via compat normalization) — the parity gate that
lets the token engine eventually retire the walker.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import (
    OutboxWriter,
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowTokenRepository,
    WorkflowVersionRepository,
)
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.engine import TokenEngine
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_V2_DEFINITION = {
    "schema_version": 2,
    "nodes": [
        {"id": "start", "type": "trigger", "data": {"operations": ["update"], "field_filter": ["status"]}},
        {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive", "expr": {"==": [{"var": "after.status"}, "closed"]}}},
        {"id": "t", "type": "task", "data": {"task_type": "service", "action_type": "update_record_field", "config": {"field": "title", "value": "DONE-V2"}}},
        {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
    ],
    "edges": [
        {"id": "e0", "source": "start", "target": "gw"},
        {"id": "e1", "source": "gw", "target": "t", "source_handle": "true"},
        {"id": "e2", "source": "t", "target": "end"},
    ],
}


async def test_v2_workflow_runs_via_outbox_dispatch(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WV2-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Ticket",
            slug="ticket",
            fields=[
                EntityFieldCreate(name="Title", slug="title", field_type="text", is_required=True),
                EntityFieldCreate(name="Status", slug="status", field_type="text"),
            ],
        )
    )
    await admin_session.commit()

    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    workflow = await wf_repo.create(name="Close v2", entity_definition_id=definition.id, description=None)
    version = await ver_repo.create(workflow_id=workflow.id, version_number=1, definition=_V2_DEFINITION)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(workflow, enabled=True, active_version_id=version.id)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields, outbox=OutboxWriter(admin_session))
    created = await repo.create({"title": "hello", "status": "open"})
    await repo.update(uuid.UUID(str(created["id"])), {"status": "closed"})
    await admin_session.commit()

    counters = await WorkflowDispatchService(admin_session).process_pending()
    await admin_session.commit()
    assert counters["runs"] == 1

    # The token-engine task mutated the record.
    await set_tenant(admin_session, str(org.id))
    fresh = await repo.get(uuid.UUID(str(created["id"])))
    assert fresh["title"] == "DONE-V2"

    run_repo = WorkflowRunRepository(admin_session, org.id)
    run = (await run_repo.list_for_workflow(workflow.id))[0]
    assert run.status == "succeeded"
    # It really went through the token engine (tokens exist for the run).
    tokens = await WorkflowTokenRepository(admin_session, org.id).list_for_run(run.id)
    assert len(tokens) >= 1
    steps = await run_repo.steps_for_run(run.id)
    assert [s.status for s in steps] == ["succeeded"]
    assert steps[0].token_id is not None


_LEGACY_DEFINITION = {
    "schema_version": 1,
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
        {"id": "c", "type": "condition", "data": {"expr": {"==": [{"var": "after.x"}, 1]}}},
        {"id": "a", "type": "action", "data": {"action_type": "log", "config": {"message": "HIT"}}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "c"},
        {"id": "e2", "source": "c", "target": "a", "source_handle": "true"},
    ],
}


async def test_walker_and_token_engine_parity_on_legacy_graph(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    """The same legacy graph, run on the walker and on the token engine (via
    compat), yields the same step output — the shadow-parity gate."""
    await set_tenant(admin_session, None)
    org = Org(name=f"WPAR-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing", slug="thing",
            fields=[EntityFieldCreate(name="X", slug="x", field_type="integer")],
        )
    )
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    workflow = await wf_repo.create(name="Parity", entity_definition_id=entity.id, description=None)
    version = await ver_repo.create(workflow_id=workflow.id, version_number=1, definition=_LEGACY_DEFINITION)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(workflow, enabled=True, active_version_id=version.id)
    await admin_session.commit()

    # --- Walker (token engine disabled) ---
    await set_tenant(admin_session, str(org.id))
    walker = WorkflowDispatchService(admin_session, token_engine_enabled=False)
    walker_run, walker_executed = await walker.run_version_manually(
        org.id, workflow, version, operation="update", record_id=None, before={"x": 0}, after={"x": 1}
    )
    await admin_session.commit()
    walker_steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(walker_run.id)

    # --- Token engine (forced on the legacy graph via compat) ---
    await set_tenant(admin_session, str(org.id))
    run = await WorkflowRunRepository(admin_session, org.id).create_run_if_absent(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=func.now(),
        trigger_operation="update",
        record_id=None,
        input_snapshot={"before": {"x": 0}, "after": {"x": 1}},
        depth=0,
    )
    await admin_session.commit()
    engine = TokenEngine(admin_session)
    await engine.start_run(run, _LEGACY_DEFINITION)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()
    engine_steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)

    # Both paths took the true branch and logged the same output.
    assert [s.output for s in walker_steps] == [{"logged": "HIT"}]
    assert [s.output for s in engine_steps] == [{"logged": "HIT"}]
    fresh = await WorkflowRunRepository(admin_session, org.id).get(run.id, run.created_at)
    assert fresh.status == "succeeded"
