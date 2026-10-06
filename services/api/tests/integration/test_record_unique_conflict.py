"""Integration tests: a unique-field conflict on a record write is a clean 409.

Creating or updating a record so that a unique field (or a one-to-one
relationship) would hold a value another record already has used to raise an
unhandled ``IntegrityError``: a 500 for the caller, and an aborted transaction for
anything else sharing the session. Against real PostgreSQL + RLS this proves:

* the repository raises ``RecordConflictError`` naming the field, without the
  conflicting value;
* the session is still usable afterwards — the failed statement ran inside a
  savepoint, so later writes in the same transaction succeed and commit;
* both record surfaces (``/api/entities`` and ``/api/v1/entities``) answer 409 on
  create and update, and a following write in the same request transaction works.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_api_key
from api.auth.dependencies import OrgContext, require_org_access
from api.config import get_settings
from api.dependencies import get_tenant_db
from api.models.custom_entity import EntityDefinition
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository, EntityRelationshipRepository
from api.repositories.dynamic_entity import DynamicEntityRepository, RecordConflictError
from api.routers import entity_records
from api.routers.v1 import records as v1_records
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate, EntityRelationshipCreate
from api.services.entity_service import EntityService
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _org_with_entities(admin_session: AsyncSession) -> tuple[Org, EntityDefinition, EntityDefinition]:
    """An org with an ``asset`` entity (unique ``code``) whose one-to-one
    ``tag`` relationship points at a ``tag`` entity."""
    await set_tenant(admin_session, None)
    org = Org(name=f"UQ-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    svc = EntityService(admin_session, org.id)
    tag = await svc.create_definition(
        EntityDefinitionCreate(
            name="Tag", slug="tag", fields=[EntityFieldCreate(name="Label", slug="label", field_type="text")]
        )
    )
    asset = await svc.create_definition(
        EntityDefinitionCreate(
            name="Asset",
            slug="asset",
            fields=[
                EntityFieldCreate(name="Code", slug="code", field_type="text", is_unique=True),
                EntityFieldCreate(name="Name", slug="name", field_type="text"),
            ],
        )
    )
    await svc.create_relationship(
        asset.id,
        EntityRelationshipCreate(name="Tag", slug="tag", cardinality="one_to_one", target_definition_id=tag.id),
    )
    await admin_session.commit()
    return org, asset, tag


async def _repo(session: AsyncSession, org_id: uuid.UUID, definition: EntityDefinition) -> DynamicEntityRepository:
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition.id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition.id)
    return DynamicEntityRepository(session, org_id, definition, fields, rels, privileged=True)


class TestRepositoryConflict:
    async def test_duplicate_unique_field_on_create(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        org, asset, _tag = await _org_with_entities(admin_session)
        await set_tenant(session, str(org.id))
        repo = await _repo(session, org.id, asset)
        await repo.create({"code": "A-1", "name": "first"})

        with pytest.raises(RecordConflictError) as exc:
            await repo.create({"code": "A-1", "name": "second"})
        assert exc.value.fields == ("code",)
        assert "A-1" not in str(exc.value)

        # The transaction survived: a further write succeeds and commits.
        await repo.create({"code": "A-2", "name": "third"})
        await session.commit()
        await set_tenant(session, str(org.id))
        items, _ = await repo.list(limit=10)
        assert sorted(r["code"] for r in items) == ["A-1", "A-2"]

    async def test_duplicate_unique_field_on_update(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        org, asset, _tag = await _org_with_entities(admin_session)
        await set_tenant(session, str(org.id))
        repo = await _repo(session, org.id, asset)
        await repo.create({"code": "A-1"})
        second = await repo.create({"code": "A-2"})
        second_id = uuid.UUID(str(second["id"]))

        with pytest.raises(RecordConflictError) as exc:
            await repo.update(second_id, {"code": "A-1"})
        assert exc.value.fields == ("code",)

        # Unchanged, and the session still works.
        assert (await repo.get(second_id))["code"] == "A-2"
        assert (await repo.update(second_id, {"name": "renamed"}))["name"] == "renamed"
        await session.commit()

    async def test_duplicate_one_to_one_relationship(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        org, asset, tag = await _org_with_entities(admin_session)
        await set_tenant(session, str(org.id))
        tags = await _repo(session, org.id, tag)
        assets = await _repo(session, org.id, asset)
        t = await tags.create({"label": "t1"})
        await assets.create({"code": "A-1", "tag": t["id"]})

        with pytest.raises(RecordConflictError) as exc:
            await assets.create({"code": "A-2", "tag": t["id"]})
        assert exc.value.fields == ("tag",)


# --------------------------------------------------------------------------- #
# HTTP: both record surfaces, on the real RLS session
# --------------------------------------------------------------------------- #
def _app(session: AsyncSession, org_id: uuid.UUID) -> FastAPI:
    """Both record routers, with auth stubbed and the RLS session injected.

    The session is shared across requests and never committed by the override, so
    a later request runs in the SAME transaction as an earlier failed one — which
    is what proves the conflict didn't poison it."""

    async def _tenant_db() -> AsyncGenerator[AsyncSession]:
        await set_tenant(session, str(org_id))
        yield session

    app = FastAPI()
    app.include_router(entity_records.router, prefix="/api/entities")
    app.include_router(v1_records.router, prefix="/api/v1/entities")
    app.dependency_overrides[require_org_access] = lambda: OrgContext(
        user=MagicMock(), org_id=org_id, membership=MagicMock(), is_org_admin=True
    )
    app.dependency_overrides[require_api_key] = lambda: ApiKeyPrincipal(
        api_key_id=uuid.uuid4(), org_id=org_id, scopes=frozenset({"records:read", "records:write"}), name="k"
    )
    app.dependency_overrides[get_tenant_db] = _tenant_db
    app.dependency_overrides[get_apikey_tenant_db] = _tenant_db
    app.dependency_overrides[get_settings] = lambda: MagicMock()
    return app


@pytest.mark.parametrize("prefix", ["/api/entities", "/api/v1/entities"])
async def test_records_api_returns_409_on_unique_conflict(
    prefix: str, admin_session: AsyncSession, session: AsyncSession
) -> None:
    org, asset, _tag = await _org_with_entities(admin_session)
    app = _app(session, org.id)
    with (
        patch.object(entity_records, "dispatch_inline_workflows", AsyncMock()),
        patch.object(v1_records, "dispatch_inline_workflows", AsyncMock()),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(f"{prefix}/asset/records", json={"code": "A-1"})
            assert first.status_code == 201

            dup = await client.post(f"{prefix}/asset/records", json={"code": "A-1"})
            assert dup.status_code == 409
            assert "'code'" in dup.json()["detail"]
            assert "A-1" not in dup.json()["detail"]

            # Same transaction as the failed insert: still usable.
            second = await client.post(f"{prefix}/asset/records", json={"code": "A-2"})
            assert second.status_code == 201

            clash = await client.patch(f"{prefix}/asset/records/{second.json()['id']}", json={"code": "A-1"})
            assert clash.status_code == 409
            assert "'code'" in clash.json()["detail"]

            ok = await client.patch(f"{prefix}/asset/records/{second.json()['id']}", json={"name": "renamed"})
            assert ok.status_code == 200

    await session.commit()
    await set_tenant(session, str(org.id))
    count = (await session.execute(text(f"SELECT count(*) FROM {asset.physical_table}"))).scalar_one()  # noqa: S608
    assert count == 2
