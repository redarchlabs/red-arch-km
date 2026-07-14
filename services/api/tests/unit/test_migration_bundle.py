"""Unit tests for the migration bundle helpers (pure, no DB).

Covers the two pieces most likely to break a round-trip silently: deep
cross-reference remapping (ids embedded in form/view configs and workflow
graphs) and collision-avoidance suffixing.
"""

from __future__ import annotations

import pytest
from api.services.migration.bundle import (
    BUNDLE_FORMAT_VERSION,
    BUNDLE_KIND,
    SUPPORTED_BUNDLE_FORMAT_VERSIONS,
    CollisionStrategy,
    IdMap,
    ImportSummary,
    filter_resources,
    remap_refs,
    suffix_name,
    suffix_slug,
)

pytestmark = pytest.mark.unit

_OLD_REL = "11111111-1111-1111-1111-111111111111"
_NEW_REL = "22222222-2222-2222-2222-222222222222"
_OLD_FORM = "33333333-3333-3333-3333-333333333333"
_NEW_FORM = "44444444-4444-4444-4444-444444444444"


def _map() -> IdMap:
    ids = IdMap()
    ids.put("relationships", _OLD_REL, _NEW_REL)
    ids.put("forms", _OLD_FORM, _NEW_FORM)
    return ids


def test_remap_rewrites_known_reference_keys_deeply() -> None:
    ids = _map()
    warnings: list[str] = []
    config = {
        "version": 2,
        "elements": [
            {"type": "section", "relationship_id": _OLD_REL, "elements": [{"type": "field", "slug": "name"}]},
            {
                "type": "button",
                "action": {"kind": "run_workflow", "workflow_id": "99999999-9999-9999-9999-999999999999"},
            },
            {"type": "form_ref", "form_id": _OLD_FORM},
        ],
    }
    out = remap_refs(config, ids, warnings)

    assert out["elements"][0]["relationship_id"] == _NEW_REL
    # Slugs and unknown keys pass through untouched.
    assert out["elements"][0]["elements"][0]["slug"] == "name"
    assert out["elements"][2]["form_id"] == _NEW_FORM
    # An unmapped reference is left as-is but flagged.
    assert out["elements"][1]["action"]["workflow_id"] == "99999999-9999-9999-9999-999999999999"
    assert any("workflow_id" in w for w in warnings)


def test_remap_ignores_non_uuid_values_under_reference_keys() -> None:
    # ``connection`` refs and slug-like values must never be treated as ids.
    ids = _map()
    warnings: list[str] = []
    node = {"entity_id": "not-a-uuid", "keep": "relationship_id-looking-string"}
    out = remap_refs(node, ids, warnings)
    assert out == node
    assert warnings == []


def test_remap_does_not_mutate_input() -> None:
    ids = _map()
    original = {"relationship_id": _OLD_REL}
    remap_refs(original, ids, [])
    assert original["relationship_id"] == _OLD_REL  # source untouched


def test_suffix_slug_avoids_collisions() -> None:
    assert suffix_slug("customer", set()) == "customer-imported"
    assert suffix_slug("customer", {"customer-imported"}) == "customer-imported-2"


def test_suffix_slug_respects_max_length() -> None:
    long = "x" * 80
    result = suffix_slug(long, set(), max_len=63)
    assert len(result) <= 63
    assert result.endswith("-imported")


def test_suffix_name_avoids_collisions() -> None:
    assert suffix_name("My Form", set()) == "My Form (imported)"
    assert suffix_name("My Form", {"My Form (imported)"}) == "My Form (imported 2)"


def test_import_summary_tallies_by_action() -> None:
    summary = ImportSummary(strategy=CollisionStrategy.SKIP)
    summary.outcome("entities").record("created")
    summary.outcome("entities").record("created")
    summary.outcome("entities").record("skipped")
    entities = summary.resources["entities"]
    assert entities.created == 2
    assert entities.skipped == 1
    assert entities.failed == 0


def test_filter_resources_keeps_only_selected_ids() -> None:
    resources = {
        "entities": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        "forms": [{"id": "f1"}, {"id": "f2"}],
        "records": [{"entity_slug": "x", "records": []}, {"entity_slug": "y", "records": []}],
    }
    out = filter_resources(resources, {"entities": ["a"], "records": ["y"]})
    assert [e["id"] for e in out["entities"]] == ["a"]
    assert [r["entity_slug"] for r in out["records"]] == ["y"]
    # A type absent from the selection is kept in full.
    assert [f["id"] for f in out["forms"]] == ["f1", "f2"]


def test_filter_resources_empty_selection_for_a_type_keeps_none() -> None:
    resources = {"entities": [{"id": "a"}, {"id": "b"}]}
    out = filter_resources(resources, {"entities": []})
    assert out["entities"] == []


def test_filter_resources_none_selection_is_passthrough() -> None:
    resources = {"entities": [{"id": "a"}]}
    assert filter_resources(resources, None) is resources


def test_bundle_constants_stable() -> None:
    # Guards against an accidental format bump that would reject old bundles.
    # v2 (lineage) is the current shape, but v1 bundles MUST still be accepted on
    # import (they simply lack lineage_id and fall back to natural-key matching).
    assert BUNDLE_KIND == "km2-migration-bundle"
    assert BUNDLE_FORMAT_VERSION == 2
    assert 1 in SUPPORTED_BUNDLE_FORMAT_VERSIONS
    assert 2 in SUPPORTED_BUNDLE_FORMAT_VERSIONS
