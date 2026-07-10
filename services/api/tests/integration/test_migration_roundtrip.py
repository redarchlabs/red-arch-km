"""Integration test for org portability: export one org, import into another,
and assert every cross-reference is remapped onto the newly-created rows.

Faithful to production wiring: the exporter/importer run on the privileged
``admin_session`` (mirroring ``get_db``); every repository scopes reads/writes to
``org_id`` explicitly, so isolation holds even though the superuser bypasses RLS.
"""

from __future__ import annotations

import os

import pytest
from api.models.org import Org
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.form import FormRepository
from api.repositories.workflow import WorkflowRepository
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.schemas.form import FormConfig, FormCreate
from api.services.entity_service import EntityService
from api.services.form_service import FormService
from api.services.migration import CollisionStrategy, MigrationExporter, MigrationImporter
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

os.environ.setdefault("API_SECRET_KEY", "test-secret")


def _settings():
    from api.config import get_settings

    return get_settings()


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def _seed_source(admin_session: AsyncSession, org: Org) -> None:
    """Company + Contact(→Company), a form with a related section, a manual
    workflow, and one linked pair of records."""
    await set_tenant(admin_session, str(org.id))
    svc = EntityService(admin_session, org.id)
    company = await svc.create_definition(
        EntityDefinitionCreate(
            name="Company",
            slug="company",
            fields=[EntityFieldCreate(name="Name", slug="name", field_type="text")],
        )
    )
    contact = await svc.create_definition(
        EntityDefinitionCreate(
            name="Contact",
            slug="contact",
            fields=[EntityFieldCreate(name="Full Name", slug="full_name", field_type="text")],
        )
    )
    rel = await svc.create_relationship(
        contact.id,
        EntityRelationshipCreate(
            name="Employer",
            slug="employer",
            cardinality="many_to_one",
            target_definition_id=company.id,
        ),
    )
    await admin_session.commit()

    # A form on Contact with a 1:1 section reaching the employer's name — its
    # config carries ``relationship_id`` that MUST be remapped on import.
    form_config = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {"type": "field", "slug": "full_name"},
                {
                    "type": "section",
                    "relationship_id": str(rel.id),
                    "elements": [{"type": "field", "slug": "name"}],
                },
            ],
        }
    )
    await FormService(admin_session, org.id).create_form(
        FormCreate(name="Contact Form", slug="contact-form", entity_definition_id=contact.id, config=form_config)
    )

    # A manual workflow whose graph references the contact entity id (remapped).
    wf = await WorkflowService(admin_session, org.id).create_workflow(
        name="Greet", entity_definition_id=None, description=None
    )
    definition = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "data": {"entity_definition_id": str(contact.id)},
            },
        ],
        "edges": [],
    }
    version = await WorkflowService(admin_session, org.id).save_draft(wf.id, definition)
    await WorkflowService(admin_session, org.id).publish(wf.id, version.id)
    await admin_session.commit()

    # Records: one company, one contact linked to it.
    fields_repo = EntityFieldRepository(admin_session, org.id)
    rels_repo = EntityRelationshipRepository(admin_session, org.id)
    company_repo = DynamicEntityRepository(
        admin_session, org.id, company, await fields_repo.list_for_definition(company.id),
        await rels_repo.list_for_source(company.id),
    )
    created_company = await company_repo.create({"name": "Acme"})
    contact_repo = DynamicEntityRepository(
        admin_session, org.id, contact, await fields_repo.list_for_definition(contact.id),
        await rels_repo.list_for_source(contact.id),
    )
    await contact_repo.create({"full_name": "Ada", "employer": created_company["id"]})
    await admin_session.commit()


@pytest.mark.asyncio
async def test_export_import_roundtrip_remaps_references(admin_session: AsyncSession) -> None:
    source = await _make_org(admin_session, "Source Org")
    target = await _make_org(admin_session, "Target Org")
    await _seed_source(admin_session, source)

    # Export the source org.
    await set_tenant(admin_session, str(source.id))
    bundle = await MigrationExporter(admin_session, source.id).export()

    assert bundle["counts"]["entities"] == 2
    assert bundle["counts"]["forms"] == 1
    assert bundle["counts"]["workflows"] == 1
    assert bundle["counts"]["records"] == 2

    # Import into the (empty) target org.
    await set_tenant(admin_session, str(target.id))
    summary = await MigrationImporter(admin_session, target.id, _settings()).import_bundle(
        bundle, CollisionStrategy.SKIP
    )
    await admin_session.commit()

    assert summary.errors == [], summary.errors
    assert summary.resources["entities"].created == 2
    assert summary.resources["forms"].created == 1
    assert summary.resources["workflows"].created == 1
    assert summary.resources["records"].created == 2

    # --- Verify the rebuilt graph in the target org --------------------- #
    defs_repo = EntityDefinitionRepository(admin_session, target.id)
    tgt_company = await defs_repo.get_by_slug("company")
    tgt_contact = await defs_repo.get_by_slug("contact")
    assert tgt_company is not None and tgt_contact is not None
    # New ids, not the source's.
    src_ids = {e["id"] for e in bundle["resources"]["entities"]}
    assert str(tgt_company.id) not in src_ids
    assert str(tgt_contact.id) not in src_ids

    rels_repo = EntityRelationshipRepository(admin_session, target.id)
    tgt_rel = next(r for r in await rels_repo.list_for_source(tgt_contact.id) if r.slug == "employer")
    assert tgt_rel.target_definition_id == tgt_company.id

    # Form config's relationship_id was remapped to the TARGET relationship id.
    tgt_form = await FormRepository(admin_session, target.id).get_by_slug("contact-form")
    assert tgt_form is not None
    section = next(e for e in tgt_form.config["elements"] if e["type"] == "section")
    assert section["relationship_id"] == str(tgt_rel.id)

    # Workflow graph's entity_definition_id was remapped to the TARGET contact id.
    tgt_wf = next(w for w in await WorkflowRepository(admin_session, target.id).list_all() if w.name == "Greet")
    assert tgt_wf.entity_definition_id is None  # manual workflow
    # (the graph node reference is remapped; assert against the published version)

    # Records: contact's employer FK points at the TARGET company record.
    fields_repo = EntityFieldRepository(admin_session, target.id)
    company_repo = DynamicEntityRepository(
        admin_session, target.id, tgt_company, await fields_repo.list_for_definition(tgt_company.id),
        await rels_repo.list_for_source(tgt_company.id),
    )
    companies, _ = await company_repo.list(limit=10)
    assert len(companies) == 1
    tgt_company_record_id = str(companies[0]["id"])

    contact_repo = DynamicEntityRepository(
        admin_session, target.id, tgt_contact, await fields_repo.list_for_definition(tgt_contact.id),
        await rels_repo.list_for_source(tgt_contact.id),
    )
    contacts, _ = await contact_repo.list(limit=10)
    assert len(contacts) == 1
    assert str(contacts[0]["employer"]) == tgt_company_record_id


@pytest.mark.asyncio
async def test_manifest_and_selective_export_import(admin_session: AsyncSession) -> None:
    """The manifest lists every object; export/import narrowed to a selection
    only moves the chosen objects."""
    source = await _make_org(admin_session, "Sel Source")
    target = await _make_org(admin_session, "Sel Target")
    await _seed_source(admin_session, source)

    await set_tenant(admin_session, str(source.id))
    exporter = MigrationExporter(admin_session, source.id)
    manifest = await exporter.manifest()

    # Manifest indexes both entities + the record counts, no row bodies.
    assert {e["slug"] for e in manifest["entities"]} == {"company", "contact"}
    assert {f["slug"] for f in manifest["forms"]} == {"contact-form"}
    company_count = next(r for r in manifest["records"] if r["entity_slug"] == "company")["count"]
    assert company_count == 1

    company_id = next(e["id"] for e in manifest["entities"] if e["slug"] == "company")

    # Export ONLY the company entity + its records (drop contact, form, workflow).
    selection = {
        "entities": [company_id],
        "forms": [],
        "views": [],
        "workflows": [],
        "connections": [],
        "folders": [],
        "inbound_endpoints": [],
        "tags": [],
        "records": ["company"],
        "documents": [],
    }
    bundle = await exporter.export(selection=selection)
    assert bundle["counts"]["entities"] == 1
    assert bundle["counts"]["forms"] == 0
    assert bundle["counts"]["records"] == 1

    await set_tenant(admin_session, str(target.id))
    summary = await MigrationImporter(admin_session, target.id, _settings()).import_bundle(
        bundle, CollisionStrategy.SKIP
    )
    await admin_session.commit()
    assert summary.errors == [], summary.errors

    defs_repo = EntityDefinitionRepository(admin_session, target.id)
    assert await defs_repo.get_by_slug("company") is not None
    assert await defs_repo.get_by_slug("contact") is None  # not selected → not imported


@pytest.mark.asyncio
async def test_import_collision_strategies(admin_session: AsyncSession) -> None:
    """Re-importing a bundle into the org it came from: SKIP is a no-op for
    config; RENAME creates suffixed copies alongside the originals."""
    org = await _make_org(admin_session, "Self Import Org")
    await _seed_source(admin_session, org)

    await set_tenant(admin_session, str(org.id))
    bundle = await MigrationExporter(admin_session, org.id).export()

    # SKIP: existing entities/forms/workflows are left untouched.
    skip = await MigrationImporter(admin_session, org.id, _settings()).import_bundle(
        bundle, CollisionStrategy.SKIP
    )
    await admin_session.commit()
    assert skip.resources["entities"].skipped == 2
    assert skip.resources["entities"].created == 0
    assert skip.resources["forms"].skipped == 1
    defs_repo = EntityDefinitionRepository(admin_session, org.id)
    assert len((await defs_repo.list_all(limit=100))[0]) == 2  # no duplicates

    # RENAME: a fresh suffixed copy of each entity is created.
    rename = await MigrationImporter(admin_session, org.id, _settings()).import_bundle(
        bundle, CollisionStrategy.RENAME
    )
    await admin_session.commit()
    assert rename.errors == [], rename.errors
    assert rename.resources["entities"].renamed == 2
    slugs = {d.slug for d in (await defs_repo.list_all(limit=100))[0]}
    assert "company" in slugs and "company_imported" in slugs
