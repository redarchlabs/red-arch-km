"""Integration test: the schedule sweep's limit must count SCHEDULED workflows.

`run_due_schedules` scans every enabled+published workflow across all orgs, sorts
by least-recently-fired, slices to `limit`, and only then skips the ones with no
schedule block. On a real installation most workflows are entity- or
manually-triggered, so a few hundred unscheduled ones fill the slice and the
handful of scheduled ones never fire — and once a scheduled workflow HAS fired it
sorts last, so it fires exactly once and then starves forever.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _definition(schedule: dict | None, slug: str) -> dict:
    trigger: dict = {"operations": []}
    if schedule:
        trigger["schedule"] = schedule
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "data": trigger},
            {
                "id": "a",
                "type": "action",
                "data": {
                    "action_type": "create_record",
                    "config": {"target_slug": slug, "values": {"title": "fired"}},
                },
            },
        ],
        "edges": [{"id": "e", "source": "t", "target": "a"}],
    }


async def test_unscheduled_workflows_do_not_starve_the_scheduled_one(
    admin_session: AsyncSession,
) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-STARVE-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))

    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Note",
            slug=f"note_{uuid.uuid4().hex[:6]}",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    await admin_session.commit()

    svc = WorkflowService(admin_session, org.id)

    # Two workflows with NO schedule, created first so they sort ahead of the
    # scheduled one under the "never fired" tie.
    for i in range(2):
        wf = await svc.create_workflow(name=f"Unscheduled {i}", entity_definition_id=definition.id, description=None)
        version = await svc.save_draft(wf.id, _definition(None, definition.slug))
        await svc.publish(wf.id, version.id)

    scheduled = await svc.create_workflow(name="Scheduled", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(scheduled.id, _definition({"every_seconds": 1}, definition.slug))
    await svc.publish(scheduled.id, version.id)
    await admin_session.commit()

    # A limit smaller than the number of unscheduled workflows: if the limit is
    # applied before the has-a-schedule filter, the scheduled one never fires.
    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    counters = await disp.run_due_schedules(limit=2)
    await admin_session.commit()

    assert counters["scheduled"] == 1, "the scheduled workflow was crowded out by unscheduled ones"
