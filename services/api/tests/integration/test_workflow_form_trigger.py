"""Integration test: a trigger pinned to source="form" fires only on form
submissions, not on ordinary record edits."""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import OutboxWriter, WorkflowRepository
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def test_form_source_trigger_ignores_record_edits(admin_session: AsyncSession) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-FORMTRIG-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))

    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Lead", slug=f"lead_{uuid.uuid4().hex[:6]}",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    await admin_session.commit()

    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    record = await repo.create({"title": "before"})
    record_id = uuid.UUID(str(record["id"]))

    # Trigger fires only on form submissions.
    definition_graph = {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": ["update"], "source": "form"}},
            {"id": "a", "type": "action",
             "data": {"action_type": "update_record_field", "config": {"field": "title", "value": "FORM_FIRED"}}},
        ],
        "edges": [{"id": "e", "source": "t", "target": "a"}],
    }
    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="OnFormSubmit", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, definition_graph)
    await svc.publish(wf.id, version.id)
    await WorkflowRepository(admin_session, org.id).update(wf, enabled=True)
    await admin_session.commit()

    outbox = OutboxWriter(admin_session)
    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")

    # A normal record edit (source="record") must NOT fire the form trigger.
    await outbox.write(
        org_id=org.id, entity_definition_id=definition.id, entity_table=definition.physical_table,
        operation="update", record_id=record_id,
        before={"title": "before"}, after={"title": "edited"}, source="record",
    )
    await admin_session.commit()
    await dispatcher.process_pending()
    await admin_session.commit()
    assert (await repo.get(record_id))["title"] == "before"  # action never ran

    # A form submission (source="form") fires it.
    await outbox.write(
        org_id=org.id, entity_definition_id=definition.id, entity_table=definition.physical_table,
        operation="update", record_id=record_id,
        before={"title": "before"}, after={"title": "submitted"}, source="form",
    )
    await admin_session.commit()
    await dispatcher.process_pending()
    await admin_session.commit()
    assert (await repo.get(record_id))["title"] == "FORM_FIRED"
