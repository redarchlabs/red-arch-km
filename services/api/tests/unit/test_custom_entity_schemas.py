"""Unit tests for custom-entity schema validation."""

from __future__ import annotations

import uuid

import pytest
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from pydantic import ValidationError


class TestEntityFieldCreate:
    def test_valid_text_field(self) -> None:
        f = EntityFieldCreate(name="Email", slug="email", field_type="text")
        assert f.slug == "email"

    def test_picklist_requires_options(self) -> None:
        with pytest.raises(ValidationError):
            EntityFieldCreate(name="Status", slug="status", field_type="picklist")

    def test_picklist_with_options_ok(self) -> None:
        f = EntityFieldCreate(
            name="Status", slug="status", field_type="picklist", picklist_options=["new", "done"]
        )
        assert f.picklist_options == ["new", "done"]

    def test_options_rejected_for_non_picklist(self) -> None:
        with pytest.raises(ValidationError):
            EntityFieldCreate(name="Email", slug="email", field_type="text", picklist_options=["x"])

    @pytest.mark.parametrize("bad", ["Email", "1email", "e-mail", "email ", ""])
    def test_bad_slug_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            EntityFieldCreate(name="X", slug=bad, field_type="text")


class TestEntityRelationshipCreate:
    def test_valid(self) -> None:
        r = EntityRelationshipCreate(
            name="Orders", slug="orders", cardinality="one_to_many", target_definition_id=uuid.uuid4()
        )
        assert r.cardinality == "one_to_many"

    def test_required_to_one_cannot_set_null(self) -> None:
        with pytest.raises(ValidationError):
            EntityRelationshipCreate(
                name="Owner",
                slug="owner",
                cardinality="many_to_one",
                target_definition_id=uuid.uuid4(),
                on_delete="SET NULL",
                is_required=True,
            )

    def test_required_to_one_cascade_ok(self) -> None:
        r = EntityRelationshipCreate(
            name="Owner",
            slug="owner",
            cardinality="many_to_one",
            target_definition_id=uuid.uuid4(),
            on_delete="CASCADE",
            is_required=True,
        )
        assert r.on_delete == "CASCADE"

    def test_bad_cardinality_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntityRelationshipCreate(
                name="X", slug="x", cardinality="one_to_none", target_definition_id=uuid.uuid4()
            )


class TestEntityDefinitionCreate:
    def test_valid_with_fields(self) -> None:
        d = EntityDefinitionCreate(
            name="Customer",
            slug="customer",
            fields=[
                EntityFieldCreate(name="Name", slug="name", field_type="text"),
                EntityFieldCreate(name="Email", slug="email", field_type="text"),
            ],
        )
        assert len(d.fields) == 2

    def test_duplicate_field_slugs_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntityDefinitionCreate(
                name="Customer",
                slug="customer",
                fields=[
                    EntityFieldCreate(name="A", slug="dup", field_type="text"),
                    EntityFieldCreate(name="B", slug="dup", field_type="text"),
                ],
            )

    def test_bad_slug_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntityDefinitionCreate(name="Customer", slug="Customer")
