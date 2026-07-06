"""Integration test: a form link is single-use even under a submit race.

Two independent public sessions attempt to submit the same token. The atomic
``pending -> submitted`` claim in ``PublicFormService.submit`` serialises them on
the link row, so only one commits its record writes; the other is rejected.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.schemas.form import FormConfig, FormCreate, FormFieldConfig, GenerateLinkRequest, PublicFormSubmit
from api.services.entity_service import EntityService
from api.services.form_service import FormLinkError, FormService, PublicFormService
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def test_concurrent_double_submit_only_one_commits(
    admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"FORM-RACE-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Person",
            slug="person",
            fields=[
                EntityFieldCreate(name="Name", slug="name", field_type="text"),
                EntityFieldCreate(name="Phone", slug="phone", field_type="text"),
            ],
        )
    )
    await admin_session.commit()

    # `session` runs as the non-superuser app_user (RLS enforced); it must have the
    # tenant context set before it can see the entity catalog or write records.
    await set_tenant(session, str(org.id))
    fields = await EntityFieldRepository(session, org.id).list_for_definition(definition.id)
    record = await DynamicEntityRepository(session, org.id, definition, fields).create({"name": "Jo"})
    record_id = uuid.UUID(str(record["id"]))

    fsvc = FormService(session, org.id, public_base_url="http://app.test")
    form = await fsvc.create_form(
        FormCreate(
            name="Intake",
            slug="intake",
            entity_definition_id=definition.id,
            config=FormConfig(fields=[FormFieldConfig(slug="name"), FormFieldConfig(slug="phone")]),
        )
    )
    _link, token, _url, _sent = await fsvc.generate_link(
        form.id, GenerateLinkRequest(target_record_id=record_id)
    )
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sa, factory() as sb:
        await sa.execute(text("RESET ROLE"))
        await sb.execute(text("RESET ROLE"))
        # A claims the link (row locked, uncommitted), then commits.
        await PublicFormService(sa).submit(token, PublicFormSubmit(values={"phone": "111"}))
        await sa.commit()
        # B, running after A's commit, finds the link already submitted → rejected.
        with pytest.raises(FormLinkError):
            await PublicFormService(sb).submit(token, PublicFormSubmit(values={"phone": "222"}))
        await sb.rollback()

    # The record reflects A's submission only.
    await set_tenant(admin_session, str(org.id))
    got = await DynamicEntityRepository(admin_session, org.id, definition, fields).get(record_id)
    assert got["phone"] == "111"
