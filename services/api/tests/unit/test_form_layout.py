"""Unit tests for the pure form-layout validator + binding flattener."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from api.schemas.form import FormConfig, upgrade_legacy_form_config
from api.services import form_layout as fl
from api.services.form_layout import (
    BlockBinding,
    LayoutError,
    RelInfo,
    SectionBinding,
    TableBinding,
)


@pytest.fixture
def ids():
    return {
        "root": uuid.uuid4(),
        "child": uuid.uuid4(),
        "related": uuid.uuid4(),
        "rel_1m": uuid.uuid4(),  # child --FK--> root (1:M targeting root)
        "rel_child_rel": uuid.uuid4(),  # child --FK--> related (to-one on child)
        "rel_1to1": uuid.uuid4(),  # root --FK--> related (to-one on root)
    }


@pytest.fixture
def fields_by_entity(ids):
    return {
        ids["root"]: {"name", "due_date"},
        ids["child"]: {"qty", "product_ref"},
        ids["related"]: {"product_name"},
    }


@pytest.fixture
def rels(ids):
    return {
        ids["rel_1m"]: RelInfo(source_id=ids["child"], target_id=ids["root"]),
        ids["rel_child_rel"]: RelInfo(source_id=ids["child"], target_id=ids["related"]),
        ids["rel_1to1"]: RelInfo(source_id=ids["root"], target_id=ids["related"]),
    }


def _tree(ids):
    return FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {"type": "label", "text": "Order", "variant": "heading"},
                {"type": "field", "slug": "name", "required": True},
                {"type": "calculated", "target_slug": "due_date", "expression": {"today": []}},
                {
                    "type": "section",
                    "relationship_id": str(ids["rel_1to1"]),
                    "mode": "inline",
                    "elements": [{"type": "field", "slug": "product_name"}],
                },
                {
                    "type": "tab_group",
                    "tabs": [
                        {
                            "label": "Lines",
                            "elements": [
                                {
                                    "type": "table",
                                    "anchor_relationship_id": str(ids["rel_1m"]),
                                    "columns": [
                                        {"kind": "field", "slug": "qty"},
                                        {
                                            "kind": "related",
                                            "relationship_id": str(ids["rel_child_rel"]),
                                            "slug": "product_name",
                                            "editable": True,
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                },
                {"type": "button", "label": "Save", "action": {"kind": "submit"}},
            ],
        }
    )


def test_valid_tree_passes(ids, fields_by_entity, rels):
    cfg = _tree(ids)
    fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)  # no raise


def test_collect_relationship_ids(ids):
    cfg = _tree(ids)
    got = fl.collect_relationship_ids(cfg.elements)
    assert got == {ids["rel_1to1"], ids["rel_1m"], ids["rel_child_rel"]}


def test_unknown_field_rejected(ids, fields_by_entity, rels):
    cfg = FormConfig.model_validate({"version": 2, "elements": [{"type": "field", "slug": "nope"}]})
    with pytest.raises(LayoutError):
        fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)


def test_table_anchor_must_target_root(ids, fields_by_entity, rels):
    # rel_1to1 is a to-one on root, illegal as a 1:M table anchor.
    cfg = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {"type": "table", "anchor_relationship_id": str(ids["rel_1to1"]), "columns": []}
            ],
        }
    )
    with pytest.raises(LayoutError):
        fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)


def test_related_column_must_hang_off_child(ids, fields_by_entity, rels):
    # rel_1to1 is a to-one on root, not on the table's child entity.
    cfg = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "type": "table",
                    "anchor_relationship_id": str(ids["rel_1m"]),
                    "columns": [
                        {
                            "kind": "related",
                            "relationship_id": str(ids["rel_1to1"]),
                            "slug": "product_name",
                        }
                    ],
                }
            ],
        }
    )
    with pytest.raises(LayoutError):
        fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)


def test_flatten_bindings(ids, rels):
    cfg = _tree(ids)
    b = fl.flatten(cfg.elements, rels)
    # root: one editable field + one persisted calc
    assert b.root.write_slugs == {"name"}
    assert [c.target_slug for c in b.root.calc] == ["due_date"]
    kinds = {type(c).__name__ for c in b.containers}
    assert kinds == {"SectionBinding", "TableBinding"}
    section = next(c for c in b.containers if isinstance(c, SectionBinding))
    assert section.entity_id == ids["related"] and section.write_slugs == {"product_name"}
    table = next(c for c in b.containers if isinstance(c, TableBinding))
    assert table.entity_id == ids["child"]
    assert table.write_slugs == {"qty"}
    assert len(table.related_cols) == 1
    rc = table.related_cols[0]
    assert rc.entity_id == ids["related"] and rc.editable and rc.slug == "product_name"


def test_read_only_field_excluded_from_write(ids, rels):
    cfg = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {"type": "field", "slug": "name"},
                {"type": "field", "slug": "due_date", "read_only": True},
            ],
        }
    )
    b = fl.flatten(cfg.elements, rels)
    assert b.root.write_slugs == {"name"}
    assert b.root.display_slugs == ["name", "due_date"]


def test_link_column_binds_no_data_but_fetches_token_fields(ids, fields_by_entity, rels):
    cfg = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "type": "table",
                    "anchor_relationship_id": str(ids["rel_1m"]),
                    "columns": [
                        {"kind": "field", "slug": "qty"},
                        {
                            "kind": "link",
                            "href_template": "/documents/{product_ref}?row={id}",
                            "link_label": "Open",
                        },
                    ],
                }
            ],
        }
    )
    # A link column binds no entity field, so validation passes without requiring
    # the template's tokens to be real fields.
    fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)  # no raise
    b = fl.flatten(cfg.elements, rels)
    table = next(c for c in b.containers if isinstance(c, TableBinding))
    # `{product_ref}` is fetched into the row so the href can substitute it; `{id}`
    # is the record id (not a field). Neither is writable; no related binding added.
    assert "product_ref" in table.display_slugs
    assert "product_ref" not in table.write_slugs
    assert table.related_cols == []


def test_table_sort_by_is_carried_and_validated(ids, fields_by_entity, rels):
    cfg = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "type": "table",
                    "anchor_relationship_id": str(ids["rel_1m"]),
                    "sort_by": "qty",
                    "sort_dir": "asc",
                    "columns": [{"kind": "field", "slug": "qty"}],
                }
            ],
        }
    )
    fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)  # qty is a child field
    b = fl.flatten(cfg.elements, rels)
    table = next(c for c in b.containers if isinstance(c, TableBinding))
    assert table.sort_by == "qty" and table.sort_dir == "asc"

    # A sort_by that isn't a field on the child entity is rejected.
    bad = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [
                {
                    "type": "table",
                    "anchor_relationship_id": str(ids["rel_1m"]),
                    "sort_by": "nope",
                    "columns": [{"kind": "field", "slug": "qty"}],
                }
            ],
        }
    )
    with pytest.raises(LayoutError):
        fl.validate(bad.elements, ids["root"], fields_by_entity, rels)


def test_link_column_rejects_dangerous_href_scheme(ids):
    anchor = str(ids["rel_1m"])
    for bad in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "vbscript:x"):
        with pytest.raises(ValidationError):
            FormConfig.model_validate(
                {
                    "version": 2,
                    "elements": [
                        {
                            "type": "table",
                            "anchor_relationship_id": anchor,
                            "columns": [{"kind": "link", "href_template": bad}],
                        }
                    ],
                }
            )
    # Relative and http(s) templates (incl. token placeholders) are accepted.
    for ok in ("/documents/{doc_key}", "https://example.com/{id}", "#section"):
        FormConfig.model_validate(
            {
                "version": 2,
                "elements": [
                    {
                        "type": "table",
                        "anchor_relationship_id": anchor,
                        "columns": [{"kind": "link", "href_template": ok}],
                    }
                ],
            }
        )


def test_progress_element_fetches_expr_fields_without_writing(ids, fields_by_entity, rels):
    cfg = FormConfig.model_validate(
        {
            "version": 2,
            "elements": [{"type": "progress", "value": {"var": "due_date"}, "max": 100}],
        }
    )
    # A progress bar binds/writes nothing, so validation passes...
    fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)  # no raise
    b = fl.flatten(cfg.elements, rels)
    # ...but the fields its expression reads are fetched (display) so the bar can
    # render them — never writable, and it adds no container binding.
    assert b.root.display_slugs == ["due_date"]
    assert b.root.write_slugs == set()
    assert not b.containers


def test_max_depth_enforced(ids, fields_by_entity, rels):
    # Nest panels beyond MAX_TREE_DEPTH.
    node: dict = {"type": "field", "slug": "name"}
    for _ in range(fl.MAX_TREE_DEPTH + 2):
        node = {"type": "panel", "elements": [node]}
    cfg = FormConfig.model_validate({"version": 2, "elements": [node]})
    with pytest.raises(LayoutError):
        fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)


# ------------------------------------------------------------------ #
# Legacy (pre-v2) {fields, sections} -> v2 {version, elements} upgrade.
# Regression: a stale legacy row used to 500 list/render on extra_forbidden.
# ------------------------------------------------------------------ #
def test_legacy_flat_config_upgrades_to_v2():
    legacy = {
        "fields": [
            {"slug": "first_name", "label": None, "required": True, "help_text": None},
            {"slug": "phone_number", "label": None, "required": None, "help_text": None},
        ],
        "sections": [],
    }
    cfg = FormConfig.model_validate(legacy)  # previously raised ValidationError
    assert cfg.version == 2
    assert [e.type for e in cfg.elements] == ["field", "field"]
    assert [e.slug for e in cfg.elements] == ["first_name", "phone_number"]
    assert cfg.elements[0].required is True
    assert cfg.elements[1].required is None


def test_legacy_field_heading_becomes_label_element():
    cfg = FormConfig.model_validate(
        {"fields": [{"slug": "email", "heading": "Contact"}], "sections": []}
    )
    assert [e.type for e in cfg.elements] == ["label", "field"]
    assert cfg.elements[0].text == "Contact"


def test_legacy_section_upgrades_to_section_element():
    rel = str(uuid.uuid4())
    cfg = FormConfig.model_validate(
        {
            "fields": [],
            "sections": [
                {
                    "relationship_id": rel,
                    "mode": "modal",
                    "label": "Address",
                    "fields": [{"slug": "line1"}],
                }
            ],
        }
    )
    assert len(cfg.elements) == 1
    sec = cfg.elements[0]
    assert sec.type == "section"
    assert str(sec.relationship_id) == rel
    assert sec.mode == "modal"
    assert [c.slug for c in sec.elements] == ["line1"]


def test_v2_config_passes_through_untouched():
    v2 = {"version": 2, "elements": [{"type": "field", "slug": "name"}]}
    assert upgrade_legacy_form_config(v2) is v2  # no-op for already-v2 payloads


def test_legacy_upgrade_is_idempotent():
    legacy = {"fields": [{"slug": "name", "required": True}], "sections": []}
    once = FormConfig.model_validate(legacy)
    twice = FormConfig.model_validate(once.model_dump(mode="json"))
    assert once.model_dump(mode="json") == twice.model_dump(mode="json")
