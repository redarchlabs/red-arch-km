"""Integration tests: ``DynamicEntityRepository.increment`` is atomic.

``update_record``'s ``increments`` used to be read-modify-write in Python — fine
for one driving process, but two writers bumping the same field could each read
the same "before" value and one write would be lost. That is exactly the shape of
a multi-station simulation (several consoles all draining the same shield pool),
so the delta is applied in SQL against the column's live value instead.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .helpers import set_tenant

pytestmark = pytest.mark.integration

WRITERS = 8


@pytest_asyncio.fixture
async def writer_engine(database_url: str) -> AsyncGenerator[AsyncEngine]:
    """A dedicated engine for the concurrent writers.

    The shared session-scoped `engine` has one connection pool, and a pooled
    connection carries its role and GUC state back to whatever test picks it up
    next (see the `session` fixture's note on committing RESET ROLE). Eight
    writers holding connections on that pool leaked enough state to fail unrelated
    retry/timer tests later in the run. A private engine, disposed here, keeps this
    test's contention entirely to itself."""
    from api.db import json_serializer

    engine = create_async_engine(database_url, echo=False, json_serializer=json_serializer)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _new_org(session: AsyncSession, prefix: str) -> Org:
    await set_tenant(session, None)
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.commit()
    await set_tenant(session, str(org.id))
    return org


async def _ship_entity(session: AsyncSession, org_id: uuid.UUID):
    definition = await EntityService(session, org_id).create_definition(
        EntityDefinitionCreate(
            name="Ship",
            slug=f"ship_{uuid.uuid4().hex[:6]}",
            fields=[
                EntityFieldCreate(name="Name", slug="name", field_type="text"),
                EntityFieldCreate(name="Shields", slug="shields", field_type="integer"),
                EntityFieldCreate(name="Heat", slug="heat", field_type="numeric"),
            ],
        )
    )
    await session.commit()
    return definition


async def _fixture(admin_session: AsyncSession, prefix: str):
    org = await _new_org(admin_session, prefix)
    definition = await _ship_entity(admin_session, org.id)
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields, privileged=True)
    return org, definition, fields, repo


async def test_concurrent_increments_do_not_lose_updates(
    admin_session: AsyncSession, writer_engine: AsyncEngine
) -> None:
    """N writers each adding +1 must leave exactly +N. Read-modify-write loses
    updates here: every writer reads the same value before any of them commits."""
    org, definition, fields, repo = await _fixture(admin_session, "INC-CC")
    record = await repo.create({"name": "Vantage", "shields": 0})
    record_id = uuid.UUID(str(record["id"]))
    await admin_session.commit()

    factory = async_sessionmaker(writer_engine, expire_on_commit=False)
    sessions = [factory() for _ in range(WRITERS)]
    try:
        repos = []
        for s in sessions:
            await set_tenant(s, str(org.id))
            repos.append(DynamicEntityRepository(s, org.id, definition, fields, privileged=True))

        async def bump(s: AsyncSession, r: DynamicEntityRepository) -> None:
            await r.increment(record_id, {"shields": 1})
            await s.commit()

        await asyncio.gather(*(bump(s, r) for s, r in zip(sessions, repos, strict=True)))
    finally:
        for s in sessions:
            await s.close()

    await set_tenant(admin_session, str(org.id))
    assert (await repo.get(record_id))["shields"] == WRITERS


async def test_increment_clamps_in_sql(admin_session: AsyncSession) -> None:
    org, definition, fields, repo = await _fixture(admin_session, "INC-CLAMP")
    record = await repo.create({"name": "Corvair", "shields": 30})
    record_id = uuid.UUID(str(record["id"]))

    after = await repo.increment(record_id, {"shields": -50}, clamps={"shields": (0, 100)})
    assert after["shields"] == 0

    after = await repo.increment(record_id, {"shields": 500}, clamps={"shields": (0, 100)})
    assert after["shields"] == 100

    # An open bound leaves that side unbounded.
    after = await repo.increment(record_id, {"shields": -500}, clamps={"shields": (None, 100)})
    assert after["shields"] == -400


async def test_increment_treats_null_as_zero(admin_session: AsyncSession) -> None:
    org, definition, fields, repo = await _fixture(admin_session, "INC-NULL")
    record = await repo.create({"name": "Meridian"})
    record_id = uuid.UUID(str(record["id"]))
    assert (await repo.increment(record_id, {"shields": 5}))["shields"] == 5


async def test_increment_applies_literal_values_in_the_same_write(
    admin_session: AsyncSession,
) -> None:
    """A field named by both `values` and `increments` takes the literal, and both
    land in ONE update so a reader never sees a half-applied change."""
    org, definition, fields, repo = await _fixture(admin_session, "INC-BOTH")
    record = await repo.create({"name": "Halcyon", "shields": 10})
    record_id = uuid.UUID(str(record["id"]))

    after = await repo.increment(record_id, {"shields": 5, "heat": 2.5}, values={"name": "Halcyon II", "shields": 99})
    assert after["name"] == "Halcyon II"
    assert after["shields"] == 99  # literal wins over the +5
    assert float(after["heat"]) == 2.5


async def test_increment_rejects_a_non_numeric_field(admin_session: AsyncSession) -> None:
    org, definition, fields, repo = await _fixture(admin_session, "INC-TYPE")
    record = await repo.create({"name": "Kestrel"})
    record_id = uuid.UUID(str(record["id"]))
    with pytest.raises(EntityRecordError):
        await repo.increment(record_id, {"name": 1})


async def test_increment_missing_record_returns_none(admin_session: AsyncSession) -> None:
    org, definition, fields, repo = await _fixture(admin_session, "INC-404")
    assert await repo.increment(uuid.uuid4(), {"shields": 1}) is None
