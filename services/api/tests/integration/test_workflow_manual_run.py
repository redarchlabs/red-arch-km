"""Integration test: manually running a published workflow performs real writes."""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

# Trigger on update; unconditionally set title = "RAN" on the changed record.
_DEFINITION = {
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
        {
            "id": "a",
            "type": "action",
            "data": {"action_type": "update_record_field", "config": {"field": "title", "value": "RAN"}},
        },
    ],
    "edges": [{"id": "e1", "source": "t", "target": "a"}],
}


async def test_manual_run_performs_real_side_effect(admin_session: AsyncSession) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-MANUAL-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))

    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Ticket", slug="ticket",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    await admin_session.commit()

    # A real record to mutate.
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    record = await repo.create({"title": "before"})
    record_id = uuid.UUID(str(record["id"]))

    # Publish a workflow.
    svc = WorkflowService(admin_session, org.id)
    workflow = await svc.create_workflow(name="Manual", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(workflow.id, _DEFINITION)
    await svc.publish(workflow.id, version.id)
    await admin_session.commit()

    # Run it for real against the record.
    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, executed = await dispatcher.run_version_manually(
        org.id, workflow, version,
        operation="update", record_id=record_id,
        before={"title": "before"}, after={"title": "before"},
    )
    await admin_session.commit()

    assert run.status == "succeeded"
    assert executed == 1
    # The action really mutated the record.
    updated = await repo.get(record_id)
    assert updated is not None
    assert updated["title"] == "RAN"
