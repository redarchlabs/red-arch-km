"""Integration test for the record-list ``@me`` filter against a real database.

Proves the security-relevant guarantee end-to-end: ``resolve_me_filters`` turns a
``owner:eq:@me`` clause into the CALLER's own record id (resolved by a
case-insensitive email match through the live relationship + entity catalog under
RLS), so a "my rows" board is scoped to the caller — and fails CLOSED (empty) for
an unknown caller rather than leaking every row.
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
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.services.entity_records_helpers import ME_FILTER_SENTINEL, resolve_me_filters
from api.services.entity_service import EntityService
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_NIL = str(uuid.UUID(int=0))


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _load_repo(
    session: AsyncSession, org_id: uuid.UUID, definition_id: uuid.UUID
) -> DynamicEntityRepository:
    definition = await EntityDefinitionRepository(session, org_id).get(definition_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition_id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition_id)
    return DynamicEntityRepository(session, org_id, definition, fields, rels)


class TestMeFilterIntegration:
    async def test_me_scopes_rows_to_the_caller(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "ME-FILTER")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)
        # A "person" keyed by email + a "note" owned by a person (the shape the LMS
        # uses: certification/simulation_attempt -> learner).
        person = await svc.create_definition(
            EntityDefinitionCreate(
                name="Person",
                slug="person",
                fields=[EntityFieldCreate(name="Email", slug="email", field_type="text", is_unique=True)],
            )
        )
        note = await svc.create_definition(
            EntityDefinitionCreate(
                name="Note",
                slug="note",
                fields=[EntityFieldCreate(name="Body", slug="body", field_type="text")],
            )
        )
        await svc.create_relationship(
            note.id,
            EntityRelationshipCreate(
                name="Owner",
                slug="owner",
                cardinality="many_to_one",
                target_definition_id=person.id,
                on_delete="CASCADE",
            ),
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        person_repo = await _load_repo(session, org.id, person.id)
        # Mixed case on purpose — the resolver matches email case-insensitively.
        alice = await person_repo.create({"email": "Alice@Example.com"})
        bob = await person_repo.create({"email": "bob@example.com"})
        note_repo = await _load_repo(session, org.id, note.id)
        await note_repo.create({"body": "a1", "owner": alice["id"]})
        await note_repo.create({"body": "a2", "owner": alice["id"]})
        await note_repo.create({"body": "b1", "owner": bob["id"]})
        await session.commit()

        note_def = await EntityDefinitionRepository(session, org.id).get(note.id)
        assert note_def is not None

        # @me → alice's own id (case-insensitive email), and listing scopes to her rows.
        resolved = await resolve_me_filters(
            session, org.id, note_def, [("owner", "eq", ME_FILTER_SENTINEL)], "alice@example.com"
        )
        assert resolved == [("owner", "eq", str(alice["id"]))]
        rows, _ = await note_repo.list(filters=resolved)
        assert {r["body"] for r in rows} == {"a1", "a2"}

        # Unknown caller → nil id → zero rows (fails closed, never org-wide).
        resolved_none = await resolve_me_filters(
            session, org.id, note_def, [("owner", "eq", ME_FILTER_SENTINEL)], "ghost@example.com"
        )
        assert resolved_none == [("owner", "eq", _NIL)]
        rows_none, _ = await note_repo.list(filters=resolved_none)
        assert rows_none == []

    async def test_me_on_a_non_relation_field_is_rejected(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "ME-FILTER-BAD")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)
        person = await svc.create_definition(
            EntityDefinitionCreate(
                name="Person",
                slug="person",
                fields=[EntityFieldCreate(name="Email", slug="email", field_type="text")],
            )
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        person_def = await EntityDefinitionRepository(session, org.id).get(person.id)
        assert person_def is not None
        with pytest.raises(HTTPException) as exc:
            await resolve_me_filters(
                session, org.id, person_def, [("email", "eq", ME_FILTER_SENTINEL)], "a@b.com"
            )
        assert exc.value.status_code == 400
