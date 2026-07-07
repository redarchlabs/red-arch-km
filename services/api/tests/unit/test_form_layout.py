"""Unit tests for the pure form-layout validator + binding flattener."""

from __future__ import annotations

import uuid

import pytest

from api.schemas.form import FormConfig
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


def test_max_depth_enforced(ids, fields_by_entity, rels):
    # Nest panels beyond MAX_TREE_DEPTH.
    node: dict = {"type": "field", "slug": "name"}
    for _ in range(fl.MAX_TREE_DEPTH + 2):
        node = {"type": "panel", "elements": [node]}
    cfg = FormConfig.model_validate({"version": 2, "elements": [node]})
    with pytest.raises(LayoutError):
        fl.validate(cfg.elements, ids["root"], fields_by_entity, rels)
