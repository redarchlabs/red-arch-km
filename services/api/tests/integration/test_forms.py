"""Integration tests for the flexible form platform (v2 element tree).

Exercises the render/submit core end-to-end against a real Postgres: the public
token flow, section/table writes, read-only enforcement, and the two security
guarantees hardened after review — calculated fields are recomputed server-side
from trusted inputs (a spoofed read-only value can't forge them), and a table's
cross-entity related column can't be used to hijack an unrelated record (IDOR).

Faithful to production wiring: DDL on the privileged ``admin_session``; org-scoped
admin work on the ``app_user`` ``session`` with the tenant GUC set; the public path
on a fresh privileged session that resolves the org from the token.
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
from api.schemas.form import FormConfig, FormCreate, FormSubmit, GenerateLinkRequest
from api.services.entity_service import EntityService
from api.services.form_service import (
    FormLinkError,
    FormNotFoundError,
    FormRenderService,
    FormService,
    PublicFormService,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


def _public_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _make_customer(admin_session: AsyncSession, org: Org) -> uuid.UUID:
    await set_tenant(admin_session, str(org.id))
    definition = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Customer",
            slug="customer",
            fields=[
                EntityFieldCreate(name="Name", slug="name", field_type="text"),
                EntityFieldCreate(name="Phone", slug="phone", field_type="text"),
                EntityFieldCreate(
                    name="Status",
                    slug="status",
                    field_type="picklist",
                    picklist_options=["new", "active", "closed"],
                ),
            ],
        )
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


async def _make_form(
    session: AsyncSession, org: Org, def_id: uuid.UUID, elements: list[dict], slug: str
) -> uuid.UUID:
    await set_tenant(session, str(org.id))
    fsvc = FormService(session, org.id, public_base_url="http://app.test")
    form = await fsvc.create_form(
        FormCreate(
            name="Intake",
            slug=slug,
            entity_definition_id=def_id,
            config=FormConfig.model_validate({"version": 2, "elements": elements}),
        )
    )
    await session.commit()
    return form.id


async def _mint(session: AsyncSession, org: Org, form_id: uuid.UUID, record_id: uuid.UUID) -> str:
    await set_tenant(session, str(org.id))
    fsvc = FormService(session, org.id, public_base_url="http://app.test")
    _link, token, _url, _sent = await fsvc.generate_link(
        form_id, GenerateLinkRequest(target_record_id=record_id)
    )
    await session.commit()
    return token


async def _public_load(engine: AsyncEngine, token: str):
    async with _public_factory(engine)() as ps:
        await ps.execute(text("RESET ROLE"))
        read = await PublicFormService(ps).load(token)
        await ps.commit()
    return read


async def _public_submit(engine: AsyncEngine, token: str, payload: FormSubmit) -> None:
    async with _public_factory(engine)() as ps:
        await ps.execute(text("RESET ROLE"))
        await PublicFormService(ps).submit(token, payload)
        await ps.commit()


def _field_slugs(config: FormConfig) -> list[str]:
    return [e.slug for e in config.elements if e.type == "field"]


# --------------------------------------------------------------------------- #
# Public token flow
# --------------------------------------------------------------------------- #
class TestPublicFormFlow:
    async def test_generate_load_submit_updates_record(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-FLOW")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "John Doe"})
        form_id = await _make_form(
            session, org, def_id,
            [{"type": "field", "slug": "name"}, {"type": "field", "slug": "phone"}],
            "intake",
        )
        token = await _mint(session, org, form_id, rec)

        read = await _public_load(engine, token)
        assert read.form_name == "Intake"
        assert set(_field_slugs(read.config)) == {"name", "phone"}
        assert read.values.get("name") == "John Doe"
        assert read.status == "pending"

        await _public_submit(engine, token, FormSubmit(values={"name": "John Q. Doe", "phone": "555-1212"}))

        record = await _read_record(session, org, def_id, rec)
        assert record["name"] == "John Q. Doe"
        assert record["phone"] == "555-1212"

        # Single-use: a second submit is rejected.
        with pytest.raises(FormLinkError):
            await _public_submit(engine, token, FormSubmit(values={"name": "again"}))

    async def test_only_exposed_fields_are_written(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-EXPOSED")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "seed"})
        form_id = await _make_form(session, org, def_id, [{"type": "field", "slug": "name"}], "name-only")
        token = await _mint(session, org, form_id, rec)

        # Crafted submission tries to write the un-exposed 'phone'.
        await _public_submit(engine, token, FormSubmit(values={"name": "Renamed", "phone": "999-9999"}))

        record = await _read_record(session, org, def_id, rec)
        assert record["name"] == "Renamed"
        assert record["phone"] is None  # un-exposed field never written

    async def test_read_only_field_is_not_written(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-READONLY")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "Locked", "phone": "111"})
        form_id = await _make_form(
            session, org, def_id,
            [{"type": "field", "slug": "name"}, {"type": "field", "slug": "phone", "read_only": True}],
            "readonly",
        )
        token = await _mint(session, org, form_id, rec)

        await _public_submit(engine, token, FormSubmit(values={"name": "Changed", "phone": "hacked"}))

        record = await _read_record(session, org, def_id, rec)
        assert record["name"] == "Changed"
        assert record["phone"] == "111"  # read-only field unchanged

    async def test_expired_link_rejected(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-EXPIRED")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "x"})
        form_id = await _make_form(session, org, def_id, [{"type": "field", "slug": "name"}], "expiring")
        token = await _mint(session, org, form_id, rec)

        await set_tenant(admin_session, str(org.id))
        link_id = (
            await admin_session.execute(text("SELECT id FROM form_links WHERE org_id = :o"), {"o": org.id})
        ).scalar_one()
        db_link = await admin_session.get(FormLink, link_id)
        assert db_link is not None
        db_link.expires_at = datetime.now(UTC) - timedelta(days=1)
        await admin_session.commit()

        read = await _public_load(engine, token)
        assert read.status == "expired"
        with pytest.raises(FormLinkError):
            await _public_submit(engine, token, FormSubmit(values={"name": "y"}))

    async def test_unknown_token_not_found(self, engine: AsyncEngine) -> None:
        async with _public_factory(engine)() as ps:
            await ps.execute(text("RESET ROLE"))
            with pytest.raises(FormNotFoundError):
                await PublicFormService(ps).load("does-not-exist")


# --------------------------------------------------------------------------- #
# Calculated fields — server-authoritative (HIGH fix regression)
# --------------------------------------------------------------------------- #
class TestCalculatedServerAuthoritative:
    async def _make_order_entity(self, admin_session: AsyncSession, org: Org) -> uuid.UUID:
        await set_tenant(admin_session, str(org.id))
        definition = await EntityService(admin_session, org.id).create_definition(
            EntityDefinitionCreate(
                name="Order", slug="order",
                fields=[
                    EntityFieldCreate(name="Qty", slug="qty", field_type="integer"),
                    EntityFieldCreate(name="Unit price", slug="unit_price", field_type="numeric"),
                    EntityFieldCreate(name="Total", slug="total", field_type="numeric"),
                ],
            )
        )
        await admin_session.commit()
        return definition.id

    async def test_calculated_ignores_spoofed_readonly_input(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-CALC")
        def_id = await self._make_order_entity(admin_session, org)
        # Server truth: unit_price = 10.
        rec = await _create_record(session, org, def_id, {"qty": 1, "unit_price": 10, "total": 0})
        form_id = await _make_form(
            session, org, def_id,
            [
                {"type": "field", "slug": "qty"},
                {"type": "field", "slug": "unit_price", "read_only": True},
                {
                    "type": "calculated",
                    "target_slug": "total",
                    "result_type": "numeric",
                    "expression": {"*": [{"var": "unit_price"}, {"var": "qty"}]},
                },
            ],
            "calc",
        )
        token = await _mint(session, org, form_id, rec)

        # Attacker sets qty=5 (allowed) AND spoofs unit_price=9999 (read-only).
        await _public_submit(engine, token, FormSubmit(values={"qty": 5, "unit_price": 9999}))

        record = await _read_record(session, org, def_id, rec)
        assert int(record["qty"]) == 5
        assert float(record["unit_price"]) == 10.0  # read-only: server value kept
        # total uses the SERVER unit_price (10), not the spoofed 9999 → 10*5 = 50.
        assert float(record["total"]) == 50.0


# --------------------------------------------------------------------------- #
# Sections + cross-entity tables (incl. IDOR fix)
# --------------------------------------------------------------------------- #
class TestSectionsAndTables:
    async def test_submit_creates_linked_and_child_records(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-SECTIONS")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)
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

        company_id = await _create_record(session, org, company.id, {"name": "Acme"})
        form_id = await _make_form(
            session, org, company.id,
            [
                {"type": "field", "slug": "name"},
                {
                    "type": "section", "relationship_id": str(addr_rel.id), "mode": "inline",
                    "elements": [{"type": "field", "slug": "city"}],
                },
                {
                    "type": "table", "anchor_relationship_id": str(contact_rel.id),
                    "columns": [{"kind": "field", "slug": "full_name"}],
                },
            ],
            "company-intake",
        )
        token = await _mint(session, org, form_id, company_id)

        read = await _public_load(engine, token)
        kinds = {e.type for e in read.config.elements}
        assert {"section", "table"} <= kinds

        await _public_submit(
            engine, token,
            FormSubmit(
                values={"name": "Acme Inc"},
                related={
                    str(addr_rel.id): {"values": {"city": "New York"}},
                    str(contact_rel.id): {"rows": [
                        {"values": {"full_name": "Alice"}},
                        {"values": {"full_name": "Bob"}},
                    ]},
                },
            ),
        )

        company_row = await _read_record(session, org, company.id, company_id)
        assert company_row["name"] == "Acme Inc"
        linked_addr_id = company_row[addr_rel.slug]
        assert linked_addr_id is not None
        addr_row = await _read_record(session, org, address.id, uuid.UUID(str(linked_addr_id)))
        assert addr_row["city"] == "New York"

        await set_tenant(session, str(org.id))
        contact_fields = await EntityFieldRepository(session, org.id).list_for_definition(contact.id)
        contact_rels = await EntityRelationshipRepository(session, org.id).list_for_source(contact.id)
        contacts, _ = await DynamicEntityRepository(
            session, org.id, contact, contact_fields, contact_rels
        ).list(filters={contact_rel.slug: company_id}, limit=50)
        assert {c["full_name"] for c in contacts} == {"Alice", "Bob"}

    async def test_related_column_cannot_hijack_unrelated_record(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        """A table row's editable cross-entity column must not update an arbitrary
        record supplied by id (IDOR). A forged id creates a fresh record instead."""
        org = await _make_org(admin_session, "FORM-IDOR")
        await set_tenant(admin_session, str(org.id))
        svc = EntityService(admin_session, org.id)
        order = await svc.create_definition(
            EntityDefinitionCreate(
                name="Order", slug="order",
                fields=[EntityFieldCreate(name="Ref", slug="ref", field_type="text")],
            )
        )
        line = await svc.create_definition(
            EntityDefinitionCreate(
                name="Line", slug="line",
                fields=[EntityFieldCreate(name="Qty", slug="qty", field_type="integer")],
            )
        )
        product = await svc.create_definition(
            EntityDefinitionCreate(
                name="Product", slug="product",
                fields=[EntityFieldCreate(name="Name", slug="pname", field_type="text")],
            )
        )
        await admin_session.commit()
        # Line -> Order (1:M anchor), Line -> Product (to-one, cross-entity column).
        line_order_rel = await svc.create_relationship(
            line.id,
            EntityRelationshipCreate(
                name="Order", slug="order", cardinality="many_to_one",
                target_definition_id=order.id, on_delete="CASCADE",
            ),
        )
        line_product_rel = await svc.create_relationship(
            line.id,
            EntityRelationshipCreate(
                name="Product", slug="product", cardinality="many_to_one",
                target_definition_id=product.id, on_delete="SET NULL",
            ),
        )
        await admin_session.commit()

        order_id = await _create_record(session, org, order.id, {"ref": "O1"})
        # A victim product NOT linked to any of this order's lines.
        victim_id = await _create_record(session, org, product.id, {"pname": "VICTIM"})

        form_id = await _make_form(
            session, org, order.id,
            [{
                "type": "table", "anchor_relationship_id": str(line_order_rel.id),
                "columns": [
                    {"kind": "field", "slug": "qty"},
                    {"kind": "related", "relationship_id": str(line_product_rel.id),
                     "slug": "pname", "editable": True},
                ],
            }],
            "idor",
        )
        token = await _mint(session, org, form_id, order_id)

        # Malicious submission: a new line row whose related column supplies the
        # VICTIM product id and tries to rename it.
        await _public_submit(
            engine, token,
            FormSubmit(
                values={},
                related={str(line_order_rel.id): {"rows": [{
                    "values": {"qty": 1},
                    "related": {str(line_product_rel.id): {
                        "id": str(victim_id), "values": {"pname": "PWNED"}
                    }},
                }]}},
            ),
        )

        victim = await _read_record(session, org, product.id, victim_id)
        assert victim["pname"] == "VICTIM"  # IDOR blocked: victim untouched


# --------------------------------------------------------------------------- #
# Authenticated internal render/submit (no token)
# --------------------------------------------------------------------------- #
class TestAuthenticatedFill:
    async def test_render_and_submit_on_tenant_session(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "FORM-AUTH")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "Start"})
        form_id = await _make_form(session, org, def_id, [{"type": "field", "slug": "name"}], "auth")

        await set_tenant(session, str(org.id))
        form = await FormService(session, org.id).get_form(form_id)
        renderer = FormRenderService(session, org.id)
        read = await renderer.build_render(form, rec, "editable")
        assert read.values.get("name") == "Start"

        await renderer.apply_submit(form, rec, FormSubmit(values={"name": "Finished"}))
        await session.commit()

        record = await _read_record(session, org, def_id, rec)
        assert record["name"] == "Finished"


# --------------------------------------------------------------------------- #
# Admin link revocation
# --------------------------------------------------------------------------- #
class TestLinkRevoke:
    async def test_revoke_pending_link_blocks_use(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        org = await _make_org(admin_session, "FORM-REVOKE")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "seed"})
        form_id = await _make_form(session, org, def_id, [{"type": "field", "slug": "name"}], "revoke")
        token = await _mint(session, org, form_id, rec)

        await set_tenant(session, str(org.id))
        fsvc = FormService(session, org.id)
        link = (await fsvc.list_links(form_id))[0]
        revoked = await fsvc.revoke_link(form_id, link.id)
        await session.commit()
        assert revoked.status == "revoked"

        read = await _public_load(engine, token)
        assert read.status == "revoked"
        with pytest.raises(FormLinkError):
            await _public_submit(engine, token, FormSubmit(values={"name": "x"}))

    async def test_cannot_revoke_submitted_link(
        self, admin_session: AsyncSession, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        from api.services.form_service import FormValidationError

        org = await _make_org(admin_session, "FORM-REVOKE2")
        def_id = await _make_customer(admin_session, org)
        rec = await _create_record(session, org, def_id, {"name": "seed"})
        form_id = await _make_form(session, org, def_id, [{"type": "field", "slug": "name"}], "revoke2")
        token = await _mint(session, org, form_id, rec)
        await _public_submit(engine, token, FormSubmit(values={"name": "done"}))

        await set_tenant(session, str(org.id))
        fsvc = FormService(session, org.id)
        link = (await fsvc.list_links(form_id))[0]
        with pytest.raises(FormValidationError):
            await fsvc.revoke_link(form_id, link.id)
