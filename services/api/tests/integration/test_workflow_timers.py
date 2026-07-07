"""Integration tests: delay/wait suspend+resume and scheduled workflows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import WorkflowRepository
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _make_ticket_entity(session: AsyncSession, org_id: uuid.UUID):
    definition = await EntityService(session, org_id).create_definition(
        EntityDefinitionCreate(
            name="Ticket",
            slug=f"ticket_{uuid.uuid4().hex[:6]}",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    await session.commit()
    return definition


async def _new_org(session: AsyncSession, prefix: str) -> Org:
    await set_tenant(session, None)
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.commit()
    await set_tenant(session, str(org.id))
    return org


# trigger → set title STEP1 → delay 1h → set title STEP2
_DELAY_DEF = {
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
        {"id": "a1", "type": "action",
         "data": {"action_type": "update_record_field", "config": {"field": "title", "value": "STEP1"}}},
        {"id": "d", "type": "delay", "data": {"delay_seconds": 3600}},
        {"id": "a2", "type": "action",
         "data": {"action_type": "update_record_field", "config": {"field": "title", "value": "STEP2"}}},
    ],
    "edges": [
        {"id": "e0", "source": "t", "target": "a1"},
        {"id": "e1", "source": "a1", "target": "d"},
        {"id": "e2", "source": "d", "target": "a2"},
    ],
}


async def test_delay_suspends_then_resumes(admin_session: AsyncSession) -> None:
    org = await _new_org(admin_session, "WF-DELAY")
    definition = await _make_ticket_entity(admin_session, org.id)

    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    record = await repo.create({"title": "before"})
    record_id = uuid.UUID(str(record["id"]))

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Delayed", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, _DELAY_DEF)
    await svc.publish(wf.id, version.id)
    await admin_session.commit()

    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, executed = await dispatcher.run_version_manually(
        org.id, wf, version,
        operation="update", record_id=record_id, before={"title": "before"}, after={"title": "before"},
    )
    await admin_session.commit()

    # Parked at the delay: pre-delay action ran, run is waiting, resume point saved.
    assert run.status == "waiting"
    assert executed == 1
    assert run.resume_node_id == "a2"
    assert run.resume_at is not None
    assert (await repo.get(record_id))["title"] == "STEP1"

    # Make it due, then sweep.
    run.resume_at = datetime.now(UTC) - timedelta(minutes=1)  # type: ignore[assignment]
    await admin_session.flush()
    counters = await dispatcher.resume_waiting_runs()
    await admin_session.commit()

    assert counters["resumed"] == 1
    assert run.status == "succeeded"
    assert run.resume_at is None
    # The post-delay action really ran.
    assert (await repo.get(record_id))["title"] == "STEP2"


async def test_scheduled_workflow_fires_when_due_and_not_before(admin_session: AsyncSession) -> None:
    org = await _new_org(admin_session, "WF-SCHED")
    definition = await _make_ticket_entity(admin_session, org.id)
    slug = definition.slug

    # Schedule-only trigger (no change operations) that creates a record each run.
    sched_def = {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": [], "schedule": {"every_minutes": 60}}},
            {"id": "a", "type": "action",
             "data": {"action_type": "create_record", "config": {"target_slug": slug, "values": {"title": "nightly"}}}},
        ],
        "edges": [{"id": "e0", "source": "t", "target": "a"}],
    }

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Scheduled", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, sched_def)
    await svc.publish(wf.id, version.id)
    await WorkflowRepository(admin_session, org.id).update(wf, enabled=True)
    await admin_session.commit()

    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)

    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")

    # First sweep: due (never run) → creates one record.
    await dispatcher.run_due_schedules()
    await admin_session.commit()
    items, _ = await repo.list(limit=50)
    assert [i["title"] for i in items] == ["nightly"]

    # Second sweep immediately after: not due (< 60 min) → no new record.
    await dispatcher.run_due_schedules()
    await admin_session.commit()
    items, _ = await repo.list(limit=50)
    assert len(items) == 1


async def test_cron_scheduled_workflow_fires_when_due(admin_session: AsyncSession) -> None:
    """A cron trigger (every minute) fires on the first sweep (never run → due)."""
    org = await _new_org(admin_session, "WF-CRON")
    definition = await _make_ticket_entity(admin_session, org.id)
    slug = definition.slug

    sched_def = {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": [], "schedule": {"cron": "* * * * *"}}},
            {"id": "a", "type": "action",
             "data": {"action_type": "create_record", "config": {"target_slug": slug, "values": {"title": "cronjob"}}}},
        ],
        "edges": [{"id": "e0", "source": "t", "target": "a"}],
    }
    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Cron", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, sched_def)
    await svc.publish(wf.id, version.id)
    await WorkflowRepository(admin_session, org.id).update(wf, enabled=True)
    await admin_session.commit()

    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")

    await dispatcher.run_due_schedules()
    await admin_session.commit()
    items, _ = await repo.list(limit=50)
    assert [i["title"] for i in items] == ["cronjob"]
