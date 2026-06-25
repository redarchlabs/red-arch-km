"""Tests for attribute definition schema validation."""

from __future__ import annotations

import pytest
from api.schemas.attribute import (
    AttributeDefinitionCreate,
    AttributeDefinitionUpdate,
)
from pydantic import ValidationError


class TestAttributeCreate:
    def test_freeform_default(self) -> None:
        attr = AttributeDefinitionCreate(name="Source", slug="source")
        assert attr.attribute_type == "freeform"
        assert attr.picklist_options == []
        assert attr.required is False

    def test_picklist_requires_options(self) -> None:
        with pytest.raises(ValidationError, match="at least one option"):
            AttributeDefinitionCreate(name="Quality", slug="quality", attribute_type="picklist")

    def test_picklist_with_options_ok(self) -> None:
        attr = AttributeDefinitionCreate(
            name="Quality",
            slug="quality",
            attribute_type="picklist",
            picklist_options=["High", "Medium", "Low"],
        )
        assert attr.picklist_options == ["High", "Medium", "Low"]

    def test_freeform_rejects_picklist_options(self) -> None:
        with pytest.raises(ValidationError, match="cannot have picklist_options"):
            AttributeDefinitionCreate(
                name="Source",
                slug="source",
                attribute_type="freeform",
                picklist_options=["A", "B"],
            )

    @pytest.mark.parametrize(
        "bad_slug",
        ["Source", "source-key", "123source", "source.key", "SOURCE", "source key"],
    )
    def test_invalid_slug_rejected(self, bad_slug: str) -> None:
        with pytest.raises(ValidationError):
            AttributeDefinitionCreate(name="X", slug=bad_slug)

    @pytest.mark.parametrize("good_slug", ["source", "source_key", "s", "a1_b2"])
    def test_valid_slugs_accepted(self, good_slug: str) -> None:
        attr = AttributeDefinitionCreate(name="X", slug=good_slug)
        assert attr.slug == good_slug

    def test_negative_order_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AttributeDefinitionCreate(name="X", slug="x", order=-1)


class TestAttributeUpdate:
    def test_all_fields_optional(self) -> None:
        update = AttributeDefinitionUpdate()
        assert update.name is None
        assert update.attribute_type is None

    def test_partial_update(self) -> None:
        update = AttributeDefinitionUpdate(required=True)
        assert update.required is True
        assert "required" in update.model_fields_set
        assert "name" not in update.model_fields_set

    def test_picklist_empty_options_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one option"):
            AttributeDefinitionUpdate(attribute_type="picklist", picklist_options=[])
