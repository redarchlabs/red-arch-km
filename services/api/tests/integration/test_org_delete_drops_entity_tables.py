"""Integration tests: deleting an org drops its custom-entity tables.

An org's records live in generated physical tables (``ce_<hex>`` per entity,
``cej_<hex>`` per many-to-many relationship). Deleting the org cascaded the rows
away but left every one of those tables behind, empty, forever. ``delete_org`` now
drops them in the same transaction — only the deleted org's tables, never another
org's — including entities that reference each other in a cycle.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import require_site_admin
from api.config import get_settings
from api.dependencies import get_db
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.routers import orgs
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate, EntityRelationshipCreate
from api.services.entity_service import EntityService
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org_with_tables(admin_session: AsyncSession, prefix: str) -> tuple[Org, list[str]]:
    """An org with two entities that reference each other (a to-one each way, so
    their tables form an FK cycle) plus a many-to-many join, and a record in each.
    Returns the org and every physical table it owns."""
    await set_tenant(admin_session, None)
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    svc = EntityService(admin_session, org.id)
    site = await svc.create_definition(
        EntityDefinitionCreate(
            name="Site", slug="site", fields=[EntityFieldCreate(name="Name", slug="name", field_type="text")]
        )
    )
    pump = await svc.create_definition(
        EntityDefinitionCreate(
            name="Pump",
            slug="pump",
            fields=[EntityFieldCreate(name="Code", slug="code", field_type="text", is_unique=True)],
        )
    )
    await svc.create_relationship(
        pump.id,
        EntityRelationshipCreate(name="Site", slug="site", cardinality="many_to_one", target_definition_id=site.id),
    )
    await svc.create_relationship(
        site.id,
        EntityRelationshipCreate(name="Lead", slug="lead", cardinality="one_to_one", target_definition_id=pump.id),
    )
    spares = await svc.create_relationship(
        pump.id,
        EntityRelationshipCreate(
            name="Spares", slug="spares", cardinality="many_to_many", target_definition_id=site.id
        ),
    )
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(site.id)
    await DynamicEntityRepository(admin_session, org.id, site, fields, privileged=True).create({"name": "North"})
    await admin_session.commit()
    return org, [site.physical_table, pump.physical_table, spares.physical_name]


async def _existing(session: AsyncSession, tables: list[str]) -> set[str]:
    rows = await session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:names)"),
        {"names": tables},
    )
    return {r[0] for r in rows}


async def test_drop_all_tables_drops_only_this_orgs_tables(admin_session: AsyncSession) -> None:
    doomed, doomed_tables = await _org_with_tables(admin_session, "DEL")
    kept, kept_tables = await _org_with_tables(admin_session, "KEEP")
    assert await _existing(admin_session, doomed_tables + kept_tables) == set(doomed_tables + kept_tables)

    await set_tenant(admin_session, None)
    dropped = await EntityService(admin_session, doomed.id).drop_all_tables()
    await admin_session.commit()

    assert sorted(dropped) == sorted(doomed_tables)
    assert await _existing(admin_session, doomed_tables + kept_tables) == set(kept_tables)


async def test_drop_all_tables_with_no_entities_is_a_no_op(admin_session: AsyncSession) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"EMPTY-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    assert await EntityService(admin_session, org.id).drop_all_tables() == []


async def test_delete_org_route_drops_the_orgs_tables(admin_session: AsyncSession) -> None:
    doomed, doomed_tables = await _org_with_tables(admin_session, "DEL")
    kept, kept_tables = await _org_with_tables(admin_session, "KEEP")
    await set_tenant(admin_session, None)

    app = FastAPI()
    app.include_router(orgs.router, prefix="/api/orgs")
    app.dependency_overrides[require_site_admin] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: admin_session
    app.dependency_overrides[get_settings] = lambda: MagicMock()
    brain = MagicMock()
    brain.remove_tenant = AsyncMock()
    with patch.object(orgs, "BrainAPIClient", return_value=brain):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(f"/api/orgs/{doomed.id}")
    assert resp.status_code == 204
    await admin_session.commit()

    assert await _existing(admin_session, doomed_tables + kept_tables) == set(kept_tables)
    remaining = set((await admin_session.execute(select(Org.id).where(Org.id.in_([doomed.id, kept.id])))).scalars())
    assert remaining == {kept.id}


async def test_delete_org_still_succeeds_when_a_table_cannot_be_dropped(admin_session: AsyncSession) -> None:
    """Dropping the tables is best-effort: if it fails (a table owned by another
    role, a lock timeout) the org is still deleted, as it was before."""
    doomed, doomed_tables = await _org_with_tables(admin_session, "DEL")
    await set_tenant(admin_session, None)

    app = FastAPI()
    app.include_router(orgs.router, prefix="/api/orgs")
    app.dependency_overrides[require_site_admin] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: admin_session
    app.dependency_overrides[get_settings] = lambda: MagicMock()
    brain = MagicMock()
    brain.remove_tenant = AsyncMock()
    calls: list[uuid.UUID] = []

    async def failing(self: EntityService) -> list[str]:
        # A real failing statement inside the drop: the savepoint must contain it,
        # or the org delete that follows hits "current transaction is aborted".
        calls.append(self._org_id)
        await admin_session.execute(text("SELECT 1/0"))
        return []

    with (
        patch.object(orgs, "BrainAPIClient", return_value=brain),
        patch.object(EntityService, "drop_all_tables", failing),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(f"/api/orgs/{doomed.id}")
    assert resp.status_code == 204
    assert calls == [doomed.id]
    await admin_session.commit()

    assert (await admin_session.execute(select(Org.id).where(Org.id == doomed.id))).scalar_one_or_none() is None
    # Left behind, as before this change — logged for manual cleanup.
    assert await _existing(admin_session, doomed_tables) == set(doomed_tables)


async def test_delete_org_logs_the_tables_and_survives_a_malformed_name(
    admin_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed physical name in the catalog makes ``identifiers.quote`` raise
    ValueError. That must not turn the org delete into a 500, and the log must name
    the tables: once the cascade removes the catalog rows nothing else records
    which tables belonged to the org."""
    doomed, doomed_tables = await _org_with_tables(admin_session, "DEL")
    await set_tenant(admin_session, None)

    app = FastAPI()
    app.include_router(orgs.router, prefix="/api/orgs")
    app.dependency_overrides[require_site_admin] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: admin_session
    app.dependency_overrides[get_settings] = lambda: MagicMock()
    brain = MagicMock()
    brain.remove_tenant = AsyncMock()

    async def malformed(self: EntityService) -> list[str]:
        raise ValueError("unsafe identifier")

    with (
        patch.object(orgs, "BrainAPIClient", return_value=brain),
        patch.object(EntityService, "drop_all_tables", malformed),
        caplog.at_level("ERROR", logger=orgs.logger.name),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(f"/api/orgs/{doomed.id}")
    assert resp.status_code == 204
    await admin_session.commit()

    assert (await admin_session.execute(select(Org.id).where(Org.id == doomed.id))).scalar_one_or_none() is None
    logged = " ".join(record.getMessage() for record in caplog.records)
    for table in doomed_tables:
        assert table in logged


async def test_drop_tables_does_not_leave_the_short_lock_timeout_behind(admin_session: AsyncSession) -> None:
    """The drop sets a short DDL lock timeout with SET LOCAL; the rest of the
    transaction (the org delete cascade) must run with the normal setting."""
    doomed, _ = await _org_with_tables(admin_session, "DEL")
    await set_tenant(admin_session, None)
    before = (await admin_session.execute(text("SHOW lock_timeout"))).scalar_one()

    await EntityService(admin_session, doomed.id).drop_all_tables()

    assert (await admin_session.execute(text("SHOW lock_timeout"))).scalar_one() == before
