"""Integration tests for WorkflowService: draft/publish + side-effect-free dry-run."""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.models.workflow import WorkflowOutbox, WorkflowRun
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.schemas.workflow import WorkflowVersionRead
from api.services.workflow.service import WorkflowService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_DEFINITION = {
    "nodes": [
        {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
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


async def _setup(admin_session: AsyncSession) -> tuple[Org, uuid.UUID]:
    await set_tenant(admin_session, None)
    org = Org(name=f"WFS-{uuid.uuid4().hex[:8]}")
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
    return org, definition.id


async def test_draft_publish_and_dry_run(admin_session: AsyncSession) -> None:
    org, entity_id = await _setup(admin_session)
    service = WorkflowService(admin_session, org.id)

    workflow = await service.create_workflow(name="Close", entity_definition_id=entity_id, description=None)
    version = await service.save_draft(workflow.id, _DEFINITION)
    await admin_session.commit()
    assert version.version_number == 1
    assert version.status == "draft"

    # Dry-run: matched, one simulated step, and NO runs/outbox written.
    result = await service.test_version(
        version.id, operation="update", before={"status": "open"}, after={"status": "closed"}
    )
    assert result["conditions_matched"] is True
    assert len(result["steps"]) == 1
    assert result["steps"][0]["action_type"] == "update_record_field"
    assert result["condition_trace"][0]["result"] is True

    runs = (
        await admin_session.execute(
            select(func.count()).select_from(WorkflowRun).where(WorkflowRun.org_id == org.id)
        )
    ).scalar_one()
    events = (
        await admin_session.execute(
            select(func.count()).select_from(WorkflowOutbox).where(WorkflowOutbox.org_id == org.id)
        )
    ).scalar_one()
    assert runs == 0 and events == 0  # dry-run has zero side effects

    # Dry-run that doesn't match.
    miss = await service.test_version(
        version.id, operation="update", before={"status": "open"}, after={"status": "open"}
    )
    assert miss["conditions_matched"] is False
    assert miss["steps"] == []

    # Publish points the workflow at the version and marks it published.
    published = await service.publish(workflow.id, version.id)
    # Serializing here mirrors the router response and must not lazy-load the
    # server-side published_at outside the greenlet (regression: MissingGreenlet 500).
    serialized = WorkflowVersionRead.model_validate(published)
    assert serialized.published_at is not None
    await admin_session.commit()
    assert published.status == "published"
    refreshed = await service.get_workflow(workflow.id)
    assert refreshed.active_version_id == version.id
