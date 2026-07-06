"""Integration test: full outbox -> dispatch -> run -> action execution."""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityDefinitionRepository, EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import (
    OutboxWriter,
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

# Graph: on update where status becomes "closed", set title to "DONE".
_DEFINITION = {
    "schema_version": 1,
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["update"], "field_filter": ["status"]}},
        {"id": "c", "type": "condition", "data": {"expr": {"==": [{"var": "after.status"}, "closed"]}}},
        {
            "id": "a",
            "type": "action",
            "data": {"action_type": "update_record_field", "config": {"field": "title", "value": "DONE"}},
        },
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "c"},
        {"id": "e2", "source": "c", "target": "a", "source_handle": "true"},
    ],
}


# Graph: on create of a Customer, create a Patient copying first_name from the
# trigger record ($ref) and setting last_name to a literal.
_CREATE_WITH_REF_DEFINITION = {
    "schema_version": 1,
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["create"]}},
        {
            "id": "a",
            "type": "action",
            "data": {
                "action_type": "create_record",
                "config": {
                    "target_slug": "patient",
                    "values": {
                        "first_name": {"$ref": "after.first_name"},
                        "last_name": "Copied",
                    },
                },
            },
        },
    ],
    "edges": [{"id": "e1", "source": "t", "target": "a"}],
}


async def test_create_record_action_copies_field_from_trigger(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    """A create_record action resolves ``{"$ref": "after.<field>"}`` values
    against the triggering record — the feature that lets a workflow copy a
    value from the newly created entity into the record it creates."""
    await set_tenant(admin_session, None)
    org = Org(name=f"WFR-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    svc = EntityService(admin_session, org.id)
    customer = await svc.create_definition(
        EntityDefinitionCreate(
            name="Customer",
            slug="customer",
            fields=[EntityFieldCreate(name="First Name", slug="first_name", field_type="text", is_required=True)],
        )
    )
    await svc.create_definition(
        EntityDefinitionCreate(
            name="Patient",
            slug="patient",
            fields=[
                EntityFieldCreate(name="First Name", slug="first_name", field_type="text", is_required=True),
                EntityFieldCreate(name="Last Name", slug="last_name", field_type="text", is_required=True),
            ],
        )
    )
    await admin_session.commit()

    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    workflow = await wf_repo.create(
        name="Customer→Patient", entity_definition_id=customer.id, description=None
    )
    version = await ver_repo.create(
        workflow_id=workflow.id, version_number=1, definition=_CREATE_WITH_REF_DEFINITION
    )
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(workflow, enabled=True, active_version_id=version.id)
    await admin_session.commit()

    # Create a Customer — captures a create outbox event.
    await set_tenant(admin_session, str(org.id))
    cust_fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(customer.id)
    cust_repo = DynamicEntityRepository(
        admin_session, org.id, customer, cust_fields, outbox=OutboxWriter(admin_session)
    )
    await cust_repo.create({"first_name": "Jeremy"})
    await admin_session.commit()

    counters = await WorkflowDispatchService(admin_session).process_pending()
    await admin_session.commit()
    assert counters["runs"] == 1
    assert counters["actions"] == 1

    # A Patient was created with first_name copied from the trigger, last_name literal.
    await set_tenant(admin_session, str(org.id))
    patient_def = await EntityDefinitionRepository(admin_session, org.id).get_by_slug("patient")
    assert patient_def is not None
    pat_fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(patient_def.id)
    pat_repo = DynamicEntityRepository(admin_session, org.id, patient_def, pat_fields)
    patients, _ = await pat_repo.list()
    assert len(patients) == 1
    assert patients[0]["first_name"] == "Jeremy"
    assert patients[0]["last_name"] == "Copied"


async def test_workflow_fires_and_runs_action(
    admin_session: AsyncSession, session: AsyncSession
) -> None:
    # --- org + entity ---
    await set_tenant(admin_session, None)
    org = Org(name=f"WFD-{uuid.uuid4().hex[:8]}")
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

    # --- workflow + published version ---
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    workflow = await wf_repo.create(
        name="Close ticket", entity_definition_id=definition.id, description=None
    )
    version = await ver_repo.create(workflow_id=workflow.id, version_number=1, definition=_DEFINITION)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(workflow, enabled=True, active_version_id=version.id)
    await admin_session.commit()

    # --- create + update a record (both capture outbox events) ---
    await set_tenant(admin_session, str(org.id))
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(
        admin_session, org.id, definition, fields, outbox=OutboxWriter(admin_session)
    )
    created = await repo.create({"title": "hello", "status": "open"})
    await repo.update(uuid.UUID(str(created["id"])), {"status": "closed"})
    await admin_session.commit()

    # --- dispatch ---
    dispatcher = WorkflowDispatchService(admin_session)
    counters = await dispatcher.process_pending()
    await admin_session.commit()

    # The update event fired one run with one action; the create event did not
    # match (trigger operations = ["update"]).
    assert counters["runs"] == 1
    assert counters["actions"] == 1

    # The action mutated the record.
    await set_tenant(admin_session, str(org.id))
    fresh = await repo.get(uuid.UUID(str(created["id"])))
    assert fresh is not None
    assert fresh["title"] == "DONE"

    # Run + step are recorded for monitoring.
    run_repo = WorkflowRunRepository(admin_session, org.id)
    runs = await run_repo.list_for_workflow(workflow.id)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "succeeded"
    assert run.conditions_matched is True
    steps = await run_repo.steps_for_run(run.id)
    assert len(steps) == 1
    assert steps[0].status == "succeeded"
    assert steps[0].output["value"] == "DONE"

    # Idempotency: re-queue the same outbox event and re-dispatch — the run's
    # created_at is pinned to the event, so uq_wf_run_event dedupes (no dup run).
    await admin_session.execute(
        text("UPDATE workflow_outbox SET status='pending' WHERE id = :oid"), {"oid": run.outbox_id}
    )
    await admin_session.commit()
    await WorkflowDispatchService(admin_session).process_pending()
    await admin_session.commit()
    assert len(await run_repo.list_for_workflow(workflow.id)) == 1
