"""Integration tests for intake forms: the public token flow end-to-end.

Proves the riskiest guarantees of Phase 1:
- A minted link resolves the org from the token alone (no auth), prefills the
  emailed record's exposed fields, and a submission updates that record.
- The public path enforces RLS: reads/writes are scoped to the token's org.
- A link is single-use and honours expiry.

Faithful to production wiring: DDL runs on the privileged ``admin_session``;
org-scoped admin work (form + link) runs on the ``app_user`` session with the
tenant GUC set (mirrors ``get_tenant_db``); the public path runs on a fresh
privileged session (mirrors ``get_db``) that resolves the org from the token.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.models.form import FormLink
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
from api.schemas.form import (
    FormConfig,
    FormCreate,
    FormFieldConfig,
    FormSectionConfig,
    GenerateLinkRequest,
    PublicFormSubmit,
)
from api.services.entity_service import EntityService
from api.services.form_service import (
    FormLinkError,
    FormNotFoundError,
    FormService,
    PublicFormService,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


def _public_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions that mirror get_db: privileged, tenant-less. The public router
    runs on this before the token identifies the org."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def _make_entity(admin_session: AsyncSession, org: Org) -> uuid.UUID:
    """Create the Customer entity (DDL → privileged session). Returns its id."""
    await set_tenant(admin_session, str(org.id))
    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Customer",
            slug="customer",
            fields=[
                EntityFieldCreate(name="Name", slug="name", field_type="text"),
                EntityFieldCreate(name="Phone", slug="phone", field_type="text"),
            ],
        )
    )
    await admin_session.commit()
    return definition.id


async def _setup_form_and_link(
    session: AsyncSession, org: Org, def_id: uuid.UUID, exposed: list[str], slug: str
) -> tuple[uuid.UUID, str]:
    """On the app_user session (RLS enforced, like get_tenant_db): create a
    record, a form exposing ``exposed`` fields, and a link. Returns (record_id, token)."""
    await set_tenant(session, str(org.id))
    definition = await EntityDefinitionRepository(session, org.id).get(def_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org.id).list_for_definition(def_id)
    record = await DynamicEntityRepository(session, org.id, definition, fields).create({"name": "John Doe"})
    record_id = uuid.UUID(str(record["id"]))

    fsvc = FormService(session, org.id, public_base_url="http://app.test")
    form = await fsvc.create_form(
        FormCreate(
            name="Intake",
            slug=slug,
            entity_definition_id=def_id,
            config=FormConfig(fields=[FormFieldConfig(slug=s) for s in exposed]),
        )
    )
    _link, token, url, _sent = await fsvc.generate_link(form.id, GenerateLinkRequest(target_record_id=record_id))
    await session.commit()
    assert url == f"http://app.test/intake/{token}"
    return record_id, token


async def _read_record(
    session: AsyncSession, org: Org, def_id: uuid.UUID, record_id: uuid.UUID
) -> dict:
    await set_tenant(session, str(org.id))
    definition = await EntityDefinitionRepository(session, org.id).get(def_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org.id).list_for_definition(def_id)
    rels = await EntityRelationshipRepository(session, org.id).list_for_source(def_id)
    record = await DynamicEntityRepository(session, org.id, definition, fields, rels).get(record_id)
    assert record is not None
    return record


class TestPublicFormFlow:
    async def test_generate_load_submit_updates_record(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-FLOW")
        def_id = await _make_entity(admin_session, org)
        record_id, token = await _setup_form_and_link(session, org, def_id, ["name", "phone"], "intake")

        factory = _public_factory(engine)

        # Public GET: schema + prefilled values, resolved from the token alone.
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            read = await PublicFormService(ps).load(token)
            await ps.commit()
        assert read.form_name == "Intake"
        assert {f.slug for f in read.fields} == {"name", "phone"}
        assert read.values.get("name") == "John Doe"
        assert read.status == "pending"

        # Public POST: updates the emailed record.
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            await PublicFormService(ps).submit(
                token, PublicFormSubmit(values={"name": "John Q. Doe", "phone": "555-1212"})
            )
            await ps.commit()

        record = await _read_record(session, org, def_id, record_id)
        assert record["name"] == "John Q. Doe"
        assert record["phone"] == "555-1212"

        # Single-use: a second submit is rejected.
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            with pytest.raises(FormLinkError):
                await PublicFormService(ps).submit(token, PublicFormSubmit(values={"name": "again"}))

    async def test_only_exposed_fields_are_written(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-EXPOSED")
        def_id = await _make_entity(admin_session, org)
        # Form exposes only 'name'; 'phone' is not on the form.
        record_id, token = await _setup_form_and_link(session, org, def_id, ["name"], "name-only")

        factory = _public_factory(engine)
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            # A crafted submission tries to write the un-exposed 'phone' field.
            await PublicFormService(ps).submit(
                token, PublicFormSubmit(values={"name": "Renamed", "phone": "999-9999"})
            )
            await ps.commit()

        record = await _read_record(session, org, def_id, record_id)
        assert record["name"] == "Renamed"
        assert record["phone"] is None  # un-exposed field ignored, not written

    async def test_presentational_layout_round_trips(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        """Order + placeholder/width/heading survive create → public render, and
        never leak into the writable field set (they are pure presentation)."""
        org = await _make_org(admin_session, "FORM-LAYOUT")
        def_id = await _make_entity(admin_session, org)

        await set_tenant(session, str(org.id))
        definition = await EntityDefinitionRepository(session, org.id).get(def_id)
        assert definition is not None
        fields = await EntityFieldRepository(session, org.id).list_for_definition(def_id)
        record = await DynamicEntityRepository(session, org.id, definition, fields).create({"name": "Jane"})
        record_id = uuid.UUID(str(record["id"]))

        fsvc = FormService(session, org.id, public_base_url="http://app.test")
        # Declared order is phone-then-name (reversed vs entity order), with
        # presentation overrides on each.
        form = await fsvc.create_form(
            FormCreate(
                name="Layout",
                slug="layout",
                entity_definition_id=def_id,
                config=FormConfig(
                    fields=[
                        FormFieldConfig(
                            slug="phone",
                            placeholder="(555) 555-5555",
                            width="half",
                            heading="Contact details",
                        ),
                        FormFieldConfig(slug="name", width="half"),
                    ]
                ),
            )
        )
        _link, token, _url, _sent = await fsvc.generate_link(
            form.id, GenerateLinkRequest(target_record_id=record_id)
        )
        await session.commit()

        factory = _public_factory(engine)
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            read = await PublicFormService(ps).load(token)
            await ps.commit()

        # Order preserved from config (phone first), presentation echoed through.
        assert [f.slug for f in read.fields] == ["phone", "name"]
        phone = read.fields[0]
        assert phone.placeholder == "(555) 555-5555"
        assert phone.width == "half"
        assert phone.heading == "Contact details"
        assert read.fields[1].width == "half"
        assert read.fields[1].placeholder is None

    async def test_expired_link_rejected(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-EXPIRED")
        def_id = await _make_entity(admin_session, org)
        _record_id, token = await _setup_form_and_link(session, org, def_id, ["name"], "expiring")

        # Backdate expiry on the link (privileged session).
        await set_tenant(admin_session, str(org.id))
        link = (
            await admin_session.execute(
                text("SELECT id FROM form_links WHERE org_id = :o"), {"o": org.id}
            )
        ).scalar_one()
        db_link = await admin_session.get(FormLink, link)
        assert db_link is not None
        db_link.expires_at = datetime.now(UTC) - timedelta(days=1)
        await admin_session.commit()

        factory = _public_factory(engine)
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            read = await PublicFormService(ps).load(token)
            await ps.commit()
        assert read.status == "expired"

        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            with pytest.raises(FormLinkError):
                await PublicFormService(ps).submit(token, PublicFormSubmit(values={"name": "x"}))

    async def test_unknown_token_not_found(self, engine: AsyncEngine) -> None:
        factory = _public_factory(engine)
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            with pytest.raises(FormNotFoundError):
                await PublicFormService(ps).load("does-not-exist")


class TestFormSections:
    """1:1 (inline) + 1:M (table) sections write across related entities."""

    async def test_submit_creates_linked_and_child_records(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-SECTIONS")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)

        # Root + related entities.
        company = await svc.create_definition(
            EntityDefinitionCreate(
                name="Company", slug="company",
                fields=[EntityFieldCreate(name="Name", slug="name", field_type="text")],
            )
        )
        address = await svc.create_definition(
            EntityDefinitionCreate(
                name="Address", slug="address",
                fields=[EntityFieldCreate(name="City", slug="city", field_type="text")],
            )
        )
        contact = await svc.create_definition(
            EntityDefinitionCreate(
                name="Contact", slug="contact",
                fields=[EntityFieldCreate(name="Full name", slug="full_name", field_type="text")],
            )
        )
        await admin_session.commit()

        # 1:1 Company -> Address (FK on Company); 1:M Contact -> Company (FK on Contact).
        addr_rel = await svc.create_relationship(
            company.id,
            EntityRelationshipCreate(
                name="Address", slug="address", cardinality="one_to_one",
                target_definition_id=address.id, on_delete="SET NULL",
            ),
        )
        contact_rel = await svc.create_relationship(
            contact.id,
            EntityRelationshipCreate(
                name="Company", slug="company", cardinality="many_to_one",
                target_definition_id=company.id, on_delete="CASCADE",
            ),
        )
        await admin_session.commit()

        # A Company record to update, then a form with a 1:1 + a 1:M section.
        await set_tenant(session, str(org.id))
        company_fields = await EntityFieldRepository(session, org.id).list_for_definition(company.id)
        company_rec = await DynamicEntityRepository(session, org.id, company, company_fields).create(
            {"name": "Acme"}
        )
        company_id = uuid.UUID(str(company_rec["id"]))

        fsvc = FormService(session, org.id, public_base_url="http://app.test")
        form = await fsvc.create_form(
            FormCreate(
                name="Company intake",
                slug="company-intake",
                entity_definition_id=company.id,
                config=FormConfig(
                    fields=[FormFieldConfig(slug="name")],
                    sections=[
                        FormSectionConfig(
                            relationship_id=addr_rel.id, mode="inline",
                            fields=[FormFieldConfig(slug="city")],
                        ),
                        FormSectionConfig(
                            relationship_id=contact_rel.id, mode="table",
                            fields=[FormFieldConfig(slug="full_name")],
                        ),
                    ],
                ),
            )
        )
        _link, token, _url, _sent = await fsvc.generate_link(
            form.id, GenerateLinkRequest(target_record_id=company_id)
        )
        await session.commit()

        # Public load shows both sections (empty prefill so far).
        factory = _public_factory(engine)
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            read = await PublicFormService(ps).load(token)
            await ps.commit()
        modes = {s.mode for s in read.sections}
        assert modes == {"inline", "table"}
        assert all(s.rows == [] for s in read.sections if s.mode == "table")

        # Public submit: root name + a 1:1 Address + two 1:M Contacts.
        async with factory() as ps:
            await ps.execute(text("RESET ROLE"))
            await PublicFormService(ps).submit(
                token,
                PublicFormSubmit(
                    values={"name": "Acme Inc"},
                    sections={
                        str(addr_rel.id): {"values": {"city": "New York"}},
                        str(contact_rel.id): {"rows": [{"full_name": "Alice"}, {"full_name": "Bob"}]},
                    },
                ),
            )
            await ps.commit()

        # Root updated.
        company_row = await _read_record(session, org, company.id, company_id)
        assert company_row["name"] == "Acme Inc"
        # 1:1: an Address was created and linked from the Company FK.
        linked_addr_id = company_row[addr_rel.slug]
        assert linked_addr_id is not None
        addr_row = await _read_record(session, org, address.id, uuid.UUID(str(linked_addr_id)))
        assert addr_row["city"] == "New York"
        # 1:M: two Contacts created, each pointing back at the Company.
        await set_tenant(session, str(org.id))
        contact_fields = await EntityFieldRepository(session, org.id).list_for_definition(contact.id)
        contact_rels = await EntityRelationshipRepository(session, org.id).list_for_source(contact.id)
        contacts, _ = await DynamicEntityRepository(
            session, org.id, contact, contact_fields, contact_rels
        ).list(filters={contact_rel.slug: company_id}, limit=50)
        assert {c["full_name"] for c in contacts} == {"Alice", "Bob"}
        assert all(str(c[contact_rel.slug]) == str(company_id) for c in contacts)
