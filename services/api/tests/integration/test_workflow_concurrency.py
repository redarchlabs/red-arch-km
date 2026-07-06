"""Integration tests: overlapping beat sweeps must not double-fire.

Two independent privileged sessions model two concurrent worker sweeps. Because
the dispatcher CLAIMS work atomically (``FOR UPDATE SKIP LOCKED`` for resumes,
``pg_try_advisory_xact_lock`` for schedules), only one sweep does each unit of
work; the other, running against the first's still-open transaction, gets
nothing. Also covers the delay-cycle step cap.
"""

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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _new_org(session: AsyncSession, prefix: str) -> Org:
    await set_tenant(session, None)
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.commit()
    await set_tenant(session, str(org.id))
    return org


async def _ticket_entity(session: AsyncSession, org_id: uuid.UUID):
    definition = await EntityService(session, org_id).create_definition(
        EntityDefinitionCreate(
            name="Ticket",
            slug=f"ticket_{uuid.uuid4().hex[:6]}",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    await session.commit()
    return definition


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


async def test_resume_is_exactly_once_across_concurrent_sweeps(
    admin_session: AsyncSession, engine: AsyncEngine
) -> None:
    org = await _new_org(admin_session, "WF-CC-RESUME")
    definition = await _ticket_entity(admin_session, org.id)
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    record = await repo.create({"title": "before"})
    record_id = uuid.UUID(str(record["id"]))

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Delayed", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, _DELAY_DEF)
    await svc.publish(wf.id, version.id)
    await admin_session.commit()

    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, _ = await disp.run_version_manually(
        org.id, wf, version,
        operation="update", record_id=record_id, before={"title": "before"}, after={"title": "before"},
    )
    run.resume_at = datetime.now(UTC) - timedelta(minutes=1)  # type: ignore[assignment]
    await admin_session.commit()

    # Two independent privileged sessions = two concurrent sweeps.
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sa, factory() as sb:
        await sa.execute(text("RESET ROLE"))
        await sb.execute(text("RESET ROLE"))
        # Sweep A claims + resumes, but does NOT commit — it holds the row lock.
        counters_a = await WorkflowDispatchService(sa, public_base_url="http://x").resume_waiting_runs()
        # Sweep B runs against A's open transaction: the due row is locked, so
        # SKIP LOCKED yields nothing.
        counters_b = await WorkflowDispatchService(sb, public_base_url="http://x").resume_waiting_runs()
        await sa.commit()
        await sb.commit()

    assert counters_a["resumed"] == 1
    assert counters_b["resumed"] == 0

    # The post-delay action ran exactly once.
    await set_tenant(admin_session, str(org.id))
    assert (await repo.get(record_id))["title"] == "STEP2"


async def test_scheduled_fire_is_exactly_once_across_concurrent_sweeps(
    admin_session: AsyncSession, engine: AsyncEngine
) -> None:
    org = await _new_org(admin_session, "WF-CC-SCHED")
    definition = await _ticket_entity(admin_session, org.id)
    slug = definition.slug
    sched_def = {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": [], "schedule": {"every_minutes": 60}}},
            {"id": "a", "type": "action",
             "data": {"action_type": "create_record",
                      "config": {"target_slug": slug, "values": {"title": "nightly"}}}},
        ],
        "edges": [{"id": "e0", "source": "t", "target": "a"}],
    }
    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Scheduled", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, sched_def)
    await svc.publish(wf.id, version.id)
    await WorkflowRepository(admin_session, org.id).update(wf, enabled=True)
    await admin_session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sa, factory() as sb:
        await sa.execute(text("RESET ROLE"))
        await sb.execute(text("RESET ROLE"))
        # A takes the per-workflow advisory lock + fires (uncommitted).
        counters_a = await WorkflowDispatchService(sa, public_base_url="http://x").run_due_schedules()
        # B can't take the same advisory lock → skips this workflow.
        counters_b = await WorkflowDispatchService(sb, public_base_url="http://x").run_due_schedules()
        await sa.commit()
        await sb.commit()

    assert counters_a["scheduled"] == 1
    assert counters_b["scheduled"] == 0

    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    await set_tenant(admin_session, str(org.id))
    items, _ = await repo.list(limit=50)
    assert len(items) == 1  # fired exactly once


# Cycle: a1 -> delay -> a2 -> a1 (a2 points back at a1). Each resume re-enters
# with a fresh visited set, so without a cap the run would resuspend forever.
_CYCLE_DEF = {
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
        {"id": "a1", "type": "action",
         "data": {"action_type": "log", "config": {"message": "a1"}}},
        {"id": "d", "type": "delay", "data": {"delay_seconds": 0}},
        {"id": "a2", "type": "action",
         "data": {"action_type": "log", "config": {"message": "a2"}}},
    ],
    "edges": [
        {"id": "e0", "source": "t", "target": "a1"},
        {"id": "e1", "source": "a1", "target": "d"},
        {"id": "e2", "source": "d", "target": "a2"},
        {"id": "e3", "source": "a2", "target": "a1"},  # cycle back
    ],
}


async def test_delay_cycle_terminates_as_failed(admin_session: AsyncSession) -> None:
    org = await _new_org(admin_session, "WF-CYCLE")
    definition = await _ticket_entity(admin_session, org.id)
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    record = await repo.create({"title": "x"})
    record_id = uuid.UUID(str(record["id"]))

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Cycle", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, _CYCLE_DEF)
    await svc.publish(wf.id, version.id)
    await admin_session.commit()

    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, _ = await disp.run_version_manually(
        org.id, wf, version,
        operation="update", record_id=record_id, before={"title": "x"}, after={"title": "x"},
    )
    await admin_session.commit()

    # Drive resumes until the run leaves 'waiting'. The step cap must make it
    # terminate (as failed) in a bounded number of sweeps rather than forever.
    for _ in range(500):
        run.resume_at = datetime.now(UTC) - timedelta(seconds=1)  # type: ignore[assignment]
        await admin_session.commit()
        await disp.resume_waiting_runs()
        await admin_session.commit()
        await admin_session.refresh(run)
        if run.status != "waiting":
            break

    assert run.status == "failed"
    assert "max run steps" in (run.error or "")
