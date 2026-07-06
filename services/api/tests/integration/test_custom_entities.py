"""Integration tests for custom entities: runtime DDL, RLS, isolation, relationships.

These prove the riskiest guarantees of Part 1:
- ``SchemaManager`` creates a *real* table with FORCE RLS + all four
  tenant_isolation policies + the app_user grant.
- Records CRUD works under ``app_user`` (RLS enforced).
- Cross-org isolation holds: tenant B cannot see tenant A's records.
- Relationships create real FK columns / join tables.
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
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.services.entity_service import EntityConflictError, EntityService
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _load_repo(
    session: AsyncSession, org_id: uuid.UUID, definition_id: uuid.UUID
) -> DynamicEntityRepository:
    """Build a DynamicEntityRepository from the catalog (as the router would)."""
    definition = await EntityDefinitionRepository(session, org_id).get(definition_id)
    assert definition is not None
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition_id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition_id)
    return DynamicEntityRepository(session, org_id, definition, fields, rels)


class TestRuntimeTableRLS:
    async def test_create_entity_table_has_rls_and_policies(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "CE-RLS")
        await set_tenant(admin_session, str(org.id))
        service = EntityService(admin_session, org.id)
        definition = await service.create_definition(
            EntityDefinitionCreate(
                name="Customer",
                slug="customer",
                fields=[
                    EntityFieldCreate(name="Name", slug="name", field_type="text", is_required=True),
                    EntityFieldCreate(name="Email", slug="email", field_type="text", is_unique=True),
                ],
            )
        )
        await admin_session.commit()
        table = definition.physical_table

        # Table exists.
        exists = (
            await admin_session.execute(text("SELECT to_regclass(:t)"), {"t": table})
        ).scalar_one()
        assert exists is not None

        # FORCE RLS enabled.
        forced = (
            await admin_session.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
            )
        ).scalar_one()
        assert forced is True

        # All four tenant_isolation policies present.
        policies = (
            await admin_session.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
        ).scalars().all()
        assert set(policies) == {
            "tenant_isolation_select",
            "tenant_isolation_insert",
            "tenant_isolation_update",
            "tenant_isolation_delete",
        }

        # app_user has CRUD grants.
        grants = (
            await admin_session.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = :t AND grantee = 'app_user'"
                ),
                {"t": table},
            )
        ).scalars().all()
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= set(grants)

    async def test_duplicate_slug_conflicts(self, admin_session: AsyncSession) -> None:
        org = await _make_org(admin_session, "CE-DUP")
        await set_tenant(admin_session, str(org.id))
        service = EntityService(admin_session, org.id)
        await service.create_definition(EntityDefinitionCreate(name="A", slug="thing"))
        await admin_session.commit()
        with pytest.raises(EntityConflictError):
            await service.create_definition(EntityDefinitionCreate(name="B", slug="thing"))
        await admin_session.rollback()


class TestRecordCrudAndIsolation:
    async def test_crud_and_cross_org_isolation(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org_a = await _make_org(admin_session, "CE-A")
        org_b = await _make_org(admin_session, "CE-B")

        # Both orgs define their own "task" entity (separate physical tables).
        for org in (org_a, org_b):
            await set_tenant(admin_session, str(org.id))
            await EntityService(admin_session, org.id).create_definition(
                EntityDefinitionCreate(
                    name="Task",
                    slug="task",
                    fields=[EntityFieldCreate(name="Title", slug="title", field_type="text", is_required=True)],
                )
            )
            await admin_session.commit()

        # Resolve org A's definition id.
        await set_tenant(admin_session, str(org_a.id))
        def_a = await EntityDefinitionRepository(admin_session, org_a.id).get_by_slug("task")
        assert def_a is not None

        # Insert a record as tenant A via the app_user session.
        await set_tenant(session, str(org_a.id))
        repo_a = await _load_repo(session, org_a.id, def_a.id)
        created = await repo_a.create({"title": "A-task"})
        assert created["title"] == "A-task"
        assert "id" in created
        await session.commit()

        # Tenant A sees exactly its record.
        await set_tenant(session, str(org_a.id))
        repo_a = await _load_repo(session, org_a.id, def_a.id)
        items, next_cursor = await repo_a.list()
        assert len(items) == 1 and items[0]["title"] == "A-task"
        assert next_cursor is None  # only one row → no further page

        # Substring search matches on text columns; a miss returns nothing.
        found, _ = await repo_a.list(search="a-task")
        assert len(found) == 1 and found[0]["title"] == "A-task"
        missing, _ = await repo_a.list(search="nonexistent")
        assert missing == []

        # Tenant B, querying A's *physical table*, sees nothing (RLS).
        await set_tenant(session, str(org_b.id))
        repo_b_on_a = DynamicEntityRepository(
            session,
            org_b.id,
            def_a,
            await EntityFieldRepository(admin_session, org_a.id).list_for_definition(def_a.id),
        )
        items_b, _ = await repo_b_on_a.list()
        assert items_b == []

    async def test_date_and_timestamp_fields_accept_iso_strings(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """A ``date``/``timestamptz`` field must accept the ISO strings the JSON
        API delivers. Regression: values were passed to asyncpg verbatim, whose
        strict date codec raised ``'str' has no attribute 'toordinal'`` — an
        unhandled 500 that reached the browser as a bare "Network Error"."""
        org = await _make_org(admin_session, "CE-DATE")
        await set_tenant(admin_session, str(org.id))
        definition = await EntityService(admin_session, org.id).create_definition(
            EntityDefinitionCreate(
                name="Patient",
                slug="patient",
                fields=[
                    EntityFieldCreate(name="Name", slug="name", field_type="text", is_required=True),
                    EntityFieldCreate(name="DOB", slug="dob", field_type="date"),
                    EntityFieldCreate(name="Seen At", slug="seen_at", field_type="timestamptz"),
                ],
            )
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        repo = await _load_repo(session, org.id, definition.id)
        created = await repo.create(
            {"name": "Jeremy", "dob": "2026-07-05", "seen_at": "2026-07-05T14:30"}
        )
        await session.commit()

        assert str(created["dob"]) == "2026-07-05"
        assert created["seen_at"].year == 2026 and created["seen_at"].tzinfo is not None

        # A malformed date is a client error (400), never a 500.
        with pytest.raises(EntityRecordError):
            await repo.create({"name": "Bad", "dob": "not-a-date"})

    async def test_keyset_pagination_covers_all_rows_without_overlap(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "CE-PAGE")
        await set_tenant(admin_session, str(org.id))
        definition = await EntityService(admin_session, org.id).create_definition(
            EntityDefinitionCreate(
                name="Note",
                slug="note",
                fields=[EntityFieldCreate(name="Body", slug="body", field_type="text")],
            )
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        repo = await _load_repo(session, org.id, definition.id)
        for i in range(25):
            await repo.create({"body": f"note-{i:02d}"})
        await session.commit()

        # Page through in batches of 10 via the cursor; collect every row exactly once.
        await set_tenant(session, str(org.id))
        repo = await _load_repo(session, org.id, definition.id)
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            items, cursor = await repo.list(cursor=cursor, limit=10)
            seen.extend(str(r["id"]) for r in items)
            pages += 1
            if cursor is None:
                break
            assert pages < 10  # guard against a cursor that never terminates

        assert len(seen) == 25  # complete coverage
        assert len(set(seen)) == 25  # no duplicates across pages
        assert pages == 3  # 10 + 10 + 5

    async def test_picklist_and_required_validation(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "CE-VALID")
        await set_tenant(admin_session, str(org.id))
        definition = await EntityService(admin_session, org.id).create_definition(
            EntityDefinitionCreate(
                name="Ticket",
                slug="ticket",
                fields=[
                    EntityFieldCreate(name="Title", slug="title", field_type="text", is_required=True),
                    EntityFieldCreate(
                        name="Status",
                        slug="status",
                        field_type="picklist",
                        picklist_options=["open", "closed"],
                    ),
                ],
            )
        )
        await admin_session.commit()

        await set_tenant(session, str(org.id))
        repo = await _load_repo(session, org.id, definition.id)

        with pytest.raises(EntityRecordError):  # missing required 'title'
            await repo.create({"status": "open"})
        with pytest.raises(EntityRecordError):  # invalid picklist value
            await repo.create({"title": "x", "status": "bogus"})
        with pytest.raises(EntityRecordError):  # unknown field
            await repo.create({"title": "x", "nope": 1})

        ok = await repo.create({"title": "x", "status": "closed"})
        assert ok["status"] == "closed"


class TestRelationships:
    async def test_many_to_one_fk_column(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "CE-REL")
        await set_tenant(admin_session, str(org.id))
        service = EntityService(admin_session, org.id)
        customer = await service.create_definition(
            EntityDefinitionCreate(
                name="Customer", slug="customer",
                fields=[EntityFieldCreate(name="Name", slug="name", field_type="text")],
            )
        )
        order = await service.create_definition(
            EntityDefinitionCreate(
                name="Order", slug="order",
                fields=[EntityFieldCreate(name="Ref", slug="ref", field_type="text")],
            )
        )
        await admin_session.commit()

        rel = await service.create_relationship(
            order.id,
            EntityRelationshipCreate(
                name="Customer", slug="customer",
                cardinality="many_to_one", target_definition_id=customer.id, on_delete="CASCADE",
            ),
        )
        await admin_session.commit()

        # FK column exists on the order table.
        col = (
            await admin_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": order.physical_table, "c": rel.physical_name},
            )
        ).scalar_one_or_none()
        assert col == rel.physical_name

        # Insert a customer, then an order referencing it (FK enforced).
        await set_tenant(session, str(org.id))
        cust_repo = await _load_repo(session, org.id, customer.id)
        cust = await cust_repo.create({"name": "Acme"})
        order_repo = await _load_repo(session, org.id, order.id)
        made = await order_repo.create({"ref": "PO-1", "customer": cust["id"]})
        assert made["customer"] == cust["id"]

    async def test_cross_org_relationship_value_rejected(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """A relationship value pointing at another org's record must be rejected
        (Postgres FK checks bypass RLS, so the repo validates ownership)."""
        # Org A with a customer, and an order entity referencing customer.
        org_a = await _make_org(admin_session, "CE-XORG-A")
        await set_tenant(admin_session, str(org_a.id))
        svc_a = EntityService(admin_session, org_a.id)
        customer = await svc_a.create_definition(
            EntityDefinitionCreate(name="Customer", slug="customer",
                                   fields=[EntityFieldCreate(name="Name", slug="name", field_type="text")])
        )
        order = await svc_a.create_definition(
            EntityDefinitionCreate(name="Order", slug="order",
                                   fields=[EntityFieldCreate(name="Ref", slug="ref", field_type="text")])
        )
        await admin_session.commit()
        await svc_a.create_relationship(
            order.id,
            EntityRelationshipCreate(name="Customer", slug="customer",
                                     cardinality="many_to_one", target_definition_id=customer.id),
        )
        await admin_session.commit()

        # Org B with its own customer record.
        org_b = await _make_org(admin_session, "CE-XORG-B")
        await set_tenant(admin_session, str(org_b.id))
        svc_b = EntityService(admin_session, org_b.id)
        cust_b_def = await svc_b.create_definition(
            EntityDefinitionCreate(name="Customer", slug="customer",
                                   fields=[EntityFieldCreate(name="Name", slug="name", field_type="text")])
        )
        await admin_session.commit()
        await set_tenant(session, str(org_b.id))
        b_fields = await EntityFieldRepository(admin_session, org_b.id).list_for_definition(cust_b_def.id)
        foreign = await DynamicEntityRepository(session, org_b.id, cust_b_def, b_fields).create({"name": "B"})
        await session.commit()

        # Org A tries to link its order to org B's customer id → rejected.
        await set_tenant(session, str(org_a.id))
        order_fields = await EntityFieldRepository(admin_session, org_a.id).list_for_definition(order.id)
        order_rels = await EntityRelationshipRepository(admin_session, org_a.id).list_for_source(order.id)
        repo_a = DynamicEntityRepository(session, org_a.id, order, order_fields, order_rels)
        with pytest.raises(EntityRecordError):
            await repo_a.create({"ref": "PO", "customer": foreign["id"]})

    async def test_many_to_many_join_table_has_rls(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        org = await _make_org(admin_session, "CE-M2M")
        await set_tenant(admin_session, str(org.id))
        service = EntityService(admin_session, org.id)
        student = await service.create_definition(EntityDefinitionCreate(name="Student", slug="student"))
        course = await service.create_definition(EntityDefinitionCreate(name="Course", slug="course"))
        await admin_session.commit()

        rel = await service.create_relationship(
            student.id,
            EntityRelationshipCreate(
                name="Courses", slug="courses",
                cardinality="many_to_many", target_definition_id=course.id,
            ),
        )
        await admin_session.commit()

        # Join table exists with FORCE RLS.
        forced = (
            await admin_session.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :t"),
                {"t": rel.physical_name},
            )
        ).scalar_one_or_none()
        assert forced is True
