"""Integration tests for the ``record_id=me`` view-render sentinel.

An entity-bound view rendered with ``record_id=me`` resolves the current user to
their OWN record in the view's root entity by matching the entity's ``email``
field case-insensitively — powering a "My …" page with no per-user URL. These
tests drive ``ViewService.render`` on a tenant session (the router only parses
``me`` vs a UUID and passes the caller's email through), mirroring the
service-level style of ``test_forms.py``.

Covers: a matching record binds (case-insensitively); no match renders unbound
(no error); an entity with no ``email`` field renders unbound (no error).
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.schemas.form import FormConfig
from api.schemas.view import ViewCreate
from api.services.entity_service import EntityService
from api.services.view_service import ViewService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _make_learner_entity(
    admin_session: AsyncSession, org: Org, *, with_email: bool = True
) -> uuid.UUID:
    await set_tenant(admin_session, str(org.id))
    fields = [EntityFieldCreate(name="Full name", slug="full_name", field_type="text")]
    if with_email:
        fields.append(EntityFieldCreate(name="Email", slug="email", field_type="text"))
    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(name="Learner", slug="learner", fields=fields)
    )
    await admin_session.commit()
    return definition.id


async def _create_record(
    session: AsyncSession, org: Org, def_id: uuid.UUID, values: dict
) -> uuid.UUID:
    await set_tenant(session, str(org.id))
    definition = await EntityDefinitionRepository(session, org.id).get(def_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org.id).list_for_definition(def_id)
    rels = await EntityRelationshipRepository(session, org.id).list_for_source(def_id)
    rec = await DynamicEntityRepository(session, org.id, definition, fields, rels).create(values)
    return uuid.UUID(str(rec["id"]))


async def _make_view(
    session: AsyncSession, org: Org, def_id: uuid.UUID, elements: list[dict], slug: str
) -> uuid.UUID:
    await set_tenant(session, str(org.id))
    view = await ViewService(session, org.id).create_view(
        ViewCreate(
            name="My Training",
            slug=slug,
            entity_definition_id=def_id,
            config=FormConfig.model_validate({"version": 2, "elements": elements}),
        )
    )
    await session.commit()
    return view.id


class TestRenderMeSentinel:
    async def test_me_binds_matching_email_record_case_insensitively(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "VIEW-ME-MATCH")
        def_id = await _make_learner_entity(admin_session, org)
        await _create_record(
            session, org, def_id, {"full_name": "Alice", "email": "Alice@Example.COM"}
        )
        # A second, unrelated learner must not be picked up.
        await _create_record(session, org, def_id, {"full_name": "Bob", "email": "bob@example.com"})
        view_id = await _make_view(
            session, org, def_id,
            [{"type": "field", "slug": "full_name"}, {"type": "field", "slug": "email"}],
            "my-training",
        )

        await set_tenant(session, str(org.id))
        # Caller's email differs only in casing from the stored value.
        read = await ViewService(session, org.id).render(
            view_id, None, current_user_email="alice@example.com"
        )
        assert read.root_entity_id == def_id
        # Alice's record was bound, not Bob's — proving a case-insensitive match.
        assert read.values.get("full_name") == "Alice"
        assert read.values.get("email") == "Alice@Example.COM"

    async def test_me_no_match_renders_unbound(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "VIEW-ME-NOMATCH")
        def_id = await _make_learner_entity(admin_session, org)
        await _create_record(session, org, def_id, {"full_name": "Alice", "email": "alice@example.com"})
        view_id = await _make_view(
            session, org, def_id, [{"type": "field", "slug": "email"}], "my-training-nomatch"
        )

        await set_tenant(session, str(org.id))
        read = await ViewService(session, org.id).render(
            view_id, None, current_user_email="nobody@example.com"
        )
        # No matching record → unbound render (same as record_id=None), no error.
        assert read.root_entity_id == def_id
        assert read.values == {}

    async def test_me_entity_without_email_field_renders_unbound(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "VIEW-ME-NOEMAIL")
        def_id = await _make_learner_entity(admin_session, org, with_email=False)
        await _create_record(session, org, def_id, {"full_name": "Alice"})
        view_id = await _make_view(
            session, org, def_id, [{"type": "field", "slug": "full_name"}], "my-training-noemail"
        )

        await set_tenant(session, str(org.id))
        read = await ViewService(session, org.id).render(
            view_id, None, current_user_email="alice@example.com"
        )
        # Entity has no `email` field → unbound render, no error.
        assert read.root_entity_id == def_id
        assert read.values == {}
