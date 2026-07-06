"""Integration tests: record mutations write workflow_outbox rows atomically."""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.models.workflow import WorkflowOutbox
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import OutboxWriter
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _entity_with_outbox(admin_session: AsyncSession, session: AsyncSession):
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-{uuid.uuid4().hex[:8]}")
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

    await set_tenant(session, str(org.id))
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(
        session, org.id, definition, fields, outbox=OutboxWriter(session)
    )
    return org, definition, repo


class TestOutboxCapture:
    async def test_create_update_delete_write_outbox(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org, definition, repo = await _entity_with_outbox(admin_session, session)

        created = await repo.create({"title": "A", "status": "open"})
        await repo.update(uuid.UUID(str(created["id"])), {"status": "closed"})
        await repo.delete(uuid.UUID(str(created["id"])))
        await session.commit()

        await set_tenant(session, str(org.id))
        rows = (
            await session.execute(
                select(WorkflowOutbox)
                .where(WorkflowOutbox.entity_definition_id == definition.id)
                .order_by(WorkflowOutbox.seq)
            )
        ).scalars().all()

        assert [r.operation for r in rows] == ["create", "update", "delete"]

        create_row, update_row, delete_row = rows
        assert create_row.before_data is None
        assert create_row.after_data["title"] == "A"
        # Update captures both sides: status open -> closed.
        assert update_row.before_data["status"] == "open"
        assert update_row.after_data["status"] == "closed"
        # Delete captures the final state as before, and no after.
        assert delete_row.before_data["status"] == "closed"
        assert delete_row.after_data is None
        # Every row is tenant-tagged and monotonically ordered.
        assert {r.org_id for r in rows} == {org.id}
        assert [r.seq for r in rows] == sorted(r.seq for r in rows)

    async def test_no_outbox_when_writer_absent(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org, definition, _ = await _entity_with_outbox(admin_session, session)
        # A repo without an outbox writer must not emit events.
        await set_tenant(session, str(org.id))
        fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
        plain = DynamicEntityRepository(session, org.id, definition, fields)
        await plain.create({"title": "silent"})
        await session.commit()

        await set_tenant(session, str(org.id))
        count = len(
            (
                await session.execute(
                    select(WorkflowOutbox).where(WorkflowOutbox.entity_definition_id == definition.id)
                )
            ).scalars().all()
        )
        assert count == 0
