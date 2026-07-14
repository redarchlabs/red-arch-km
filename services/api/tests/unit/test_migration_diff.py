"""Unit tests for the migration diff engine (pure, no DB).

Covers the two behaviours the change-management preview depends on: lineage-first
correlation (falling back to the natural key on the first promotion) and the
content fingerprint (an id-remap between environments must NOT read as a change,
but a real content edit must).
"""

from __future__ import annotations

import pytest
from api.services.migration.diff import ObjectStatus, compute_diff

pytestmark = pytest.mark.unit


def _entity(id_: str, lineage: str, slug: str, name: str) -> dict:
    return {"id": id_, "lineage_id": lineage, "slug": slug, "name": name, "fields": [], "relationships": []}


def test_identical_config_is_unchanged() -> None:
    src = {"entities": [_entity("a", "a", "customer", "Customer")]}
    tgt = {"entities": [_entity("a", "a", "customer", "Customer")]}
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.unchanged == 1
    assert ent.added == ent.changed == ent.deleted == 0
    assert diff.has_deletes is False


def test_added_when_target_missing() -> None:
    src = {"entities": [_entity("a", "a", "customer", "Customer")]}
    tgt: dict = {"entities": []}
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.added == 1
    assert [o.status for o in ent.objects] == [ObjectStatus.ADDED]


def test_changed_when_content_differs() -> None:
    src = {"entities": [_entity("a", "a", "customer", "Customer Renamed")]}
    tgt = {"entities": [_entity("a", "a", "customer", "Customer")]}
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.changed == 1
    assert ent.objects[0].status == ObjectStatus.CHANGED


def test_deleted_when_target_has_extra() -> None:
    src: dict = {"entities": []}
    tgt = {"entities": [_entity("z", "z", "obsolete", "Obsolete")]}
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.deleted == 1
    assert diff.has_deletes is True
    assert "entities" in diff.delete_order


def test_lineage_match_survives_rename() -> None:
    # Same lineage, DIFFERENT slug (renamed on the target): correlate by lineage,
    # so it is a change, not add+delete.
    src = {"entities": [_entity("a", "shared", "customer", "Customer")]}
    tgt = {"entities": [_entity("b", "shared", "client", "Client")]}
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.changed == 1
    assert ent.added == 0 and ent.deleted == 0


def test_natural_key_fallback_on_first_promotion() -> None:
    # First promotion: source lineage == its own id, target authored independently
    # (different self-origin id) → lineage does NOT match, fall back to slug.
    src = {"entities": [_entity("src-id", "src-id", "customer", "Customer")]}
    tgt = {"entities": [_entity("tgt-id", "tgt-id", "customer", "Customer")]}
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.unchanged == 1  # same slug + same content → unchanged
    assert ent.added == 0 and ent.deleted == 0


def test_embedded_ref_remap_is_not_a_change() -> None:
    # A view references a report by UUID. Across environments the report's local id
    # differs but its lineage is the same, so the reference normalizes identically
    # and the view must read as UNCHANGED, not changed. (Ref normalization only
    # rewrites values that are real UUIDs, so these must be UUID-shaped.)
    rep_src = "11111111-1111-1111-1111-111111111111"
    rep_tgt = "22222222-2222-2222-2222-222222222222"
    rep_lin = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    view_lin = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    view_src = "33333333-3333-3333-3333-333333333333"
    view_tgt = "44444444-4444-4444-4444-444444444444"
    src = {
        "reports": [{"id": rep_src, "lineage_id": rep_lin, "slug": "sales", "name": "Sales"}],
        "views": [
            {
                "id": view_src,
                "lineage_id": view_lin,
                "slug": "dash",
                "name": "Dash",
                "config": {"children": [{"type": "report", "report_id": rep_src}]},
            }
        ],
    }
    tgt = {
        "reports": [{"id": rep_tgt, "lineage_id": rep_lin, "slug": "sales", "name": "Sales"}],
        "views": [
            {
                "id": view_tgt,
                "lineage_id": view_lin,
                "slug": "dash",
                "name": "Dash",
                "config": {"children": [{"type": "report", "report_id": rep_tgt}]},
            }
        ],
    }
    diff = compute_diff(src, tgt)
    views = next(r for r in diff.resources if r.resource_type == "views")
    assert views.unchanged == 1
    assert views.changed == 0


def test_nested_field_ids_do_not_affect_fingerprint() -> None:
    # The same entity in two environments has different field row ids; the entity
    # must still read as UNCHANGED (nested ids/lineage are stripped before hashing).
    src = {
        "entities": [
            {
                "id": "e-src",
                "lineage_id": "e-lin",
                "slug": "customer",
                "name": "Customer",
                "fields": [{"id": "f-src", "lineage_id": "f-lin", "slug": "email", "name": "Email"}],
                "relationships": [],
            }
        ]
    }
    tgt = {
        "entities": [
            {
                "id": "e-tgt",
                "lineage_id": "e-lin",
                "slug": "customer",
                "name": "Customer",
                "fields": [{"id": "f-tgt", "lineage_id": "f-lin", "slug": "email", "name": "Email"}],
                "relationships": [],
            }
        ]
    }
    diff = compute_diff(src, tgt)
    ent = next(r for r in diff.resources if r.resource_type == "entities")
    assert ent.unchanged == 1
    assert ent.changed == 0


def test_records_are_count_only() -> None:
    src = {"records": [{"entity_slug": "customer", "records": [{"id": "1"}, {"id": "2"}]}]}
    tgt: dict = {"records": []}
    diff = compute_diff(src, tgt)
    rec = next(r for r in diff.resources if r.resource_type == "records")
    assert rec.count_only is True
    assert rec.added == 2  # informational row count
    assert rec.deleted == 0  # data is never delete-flagged
