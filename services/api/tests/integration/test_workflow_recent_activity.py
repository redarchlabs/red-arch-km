"""Integration: the org-wide activity feed (WorkflowRunRepository.list_recent).

The workflows page shows recent runs across ALL workflows, each paired with its
workflow name and ordered newest-first. These cover the join + ordering and the
org-scoping that keeps one tenant's runs out of another's feed.
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
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_EMPTY_DEF = {"schema_version": 2, "nodes": [], "edges": []}


async def _make_org(admin_session: AsyncSession):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFACT-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing", slug="thing",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    return org, entity


async def _make_workflow_run(admin_session: AsyncSession, org, entity, *, name: str, created_at: datetime):
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    wf = await wf_repo.create(name=name, entity_definition_id=entity.id, description=None)
    version = await ver_repo.create(workflow_id=wf.id, version_number=1, definition=_EMPTY_DEF)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    run = await WorkflowRunRepository(admin_session, org.id).create_run_if_absent(
        workflow_id=wf.id, workflow_version_id=version.id, outbox_id=uuid.uuid4(), outbox_seq=None,
        created_at=created_at, trigger_operation="update", record_id=None,
        input_snapshot=None, depth=0,
    )
    await admin_session.commit()
    return wf, run


async def test_list_recent_returns_runs_across_workflows_newest_first(admin_session: AsyncSession) -> None:
    org, entity = await _make_org(admin_session)
    await _make_workflow_run(admin_session, org, entity, name="Alpha", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _make_workflow_run(admin_session, org, entity, name="Beta", created_at=datetime(2026, 1, 2, tzinfo=UTC))

    await set_tenant(admin_session, str(org.id))
    rows = await WorkflowRunRepository(admin_session, org.id).list_recent(limit=25)

    # Each row is (run, workflow_name); newest (Beta) first.
    assert [name for _run, name in rows] == ["Beta", "Alpha"]
    assert all(run.status == "running" for run, _name in rows)


async def test_list_recent_respects_limit(admin_session: AsyncSession) -> None:
    org, entity = await _make_org(admin_session)
    for i in range(3):
        await _make_workflow_run(
            admin_session, org, entity, name=f"WF{i}", created_at=datetime(2026, 1, i + 1, tzinfo=UTC)
        )
    await set_tenant(admin_session, str(org.id))
    rows = await WorkflowRunRepository(admin_session, org.id).list_recent(limit=2)
    assert [name for _run, name in rows] == ["WF2", "WF1"]  # two newest only


async def test_list_recent_is_org_scoped(admin_session: AsyncSession) -> None:
    org_a, entity_a = await _make_org(admin_session)
    await _make_workflow_run(admin_session, org_a, entity_a, name="A-WF", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    org_b, entity_b = await _make_org(admin_session)
    await _make_workflow_run(admin_session, org_b, entity_b, name="B-WF", created_at=datetime(2026, 1, 1, tzinfo=UTC))

    await set_tenant(admin_session, str(org_a.id))
    rows_a = await WorkflowRunRepository(admin_session, org_a.id).list_recent()
    assert [name for _run, name in rows_a] == ["A-WF"]  # org B's run is not visible
