"""Unit tests for DynamicEntityRepository scalar coercion + required-null guards.

These exercise ``_coerce_value`` / ``_to_row`` in isolation (no database): the
repository builds its SQLAlchemy ``Table`` from the catalog in ``__init__`` but
neither method touches the session, so a dummy session is sufficient. The point
is that malformed/typed input becomes a 400 (``EntityRecordError``) instead of an
unhandled asyncpg codec 500.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.services import identifiers


def _field(slug: str, field_type: str, *, required: bool = False, picklist: list[str] | None = None):  # type: ignore[no-untyped-def]
    f = MagicMock()
    f.slug = slug
    f.field_type = field_type
    f.is_required = required
    f.is_unique = False
    f.picklist_options = picklist or []
    f.physical_column = identifiers.column_name(uuid.uuid4())
    return f


def _rel(slug: str, *, required: bool = False):  # type: ignore[no-untyped-def]
    r = MagicMock()
    r.slug = slug
    r.is_required = required
    r.cardinality = "many_to_one"
    r.physical_name = identifiers.relation_column_name(uuid.uuid4())
    return r


def _repo(fields, rels=None):  # type: ignore[no-untyped-def]
    definition = MagicMock()
    definition.physical_table = identifiers.table_name(uuid.uuid4())
    definition.id = uuid.uuid4()
    return DynamicEntityRepository(MagicMock(), uuid.uuid4(), definition, fields, rels or [])


class TestScalarCoercion:
    def test_integer_valid_string_coerces(self) -> None:
        repo = _repo([_field("age", "integer")])
        row = repo._to_row({"age": "42"}, for_create=True)
        assert 42 in row.values()

    def test_integer_non_number_is_400(self) -> None:
        repo = _repo([_field("age", "integer")])
        with pytest.raises(EntityRecordError):
            repo._to_row({"age": "not-a-number"}, for_create=True)

    def test_integer_rejects_float_and_bool_strings(self) -> None:
        repo = _repo([_field("age", "integer")])
        with pytest.raises(EntityRecordError):
            repo._to_row({"age": "42.5"}, for_create=True)
        with pytest.raises(EntityRecordError):
            repo._to_row({"age": "true"}, for_create=True)

    def test_bigint_coerces(self) -> None:
        repo = _repo([_field("n", "bigint")])
        row = repo._to_row({"n": "9000000000"}, for_create=True)
        assert 9000000000 in row.values()

    def test_numeric_valid_and_invalid(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        row = repo._to_row({"amount": "42.50"}, for_create=True)
        assert Decimal("42.50") in row.values()
        with pytest.raises(EntityRecordError):
            repo._to_row({"amount": "abc"}, for_create=True)

    def test_numeric_rejects_nan_infinity(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        with pytest.raises(EntityRecordError):
            repo._to_row({"amount": "NaN"}, for_create=True)
        with pytest.raises(EntityRecordError):
            repo._to_row({"amount": "Infinity"}, for_create=True)

    def test_boolean_string_spellings(self) -> None:
        repo = _repo([_field("flag", "boolean")])
        assert True in repo._to_row({"flag": "true"}, for_create=True).values()
        assert False in repo._to_row({"flag": "no"}, for_create=True).values()
        with pytest.raises(EntityRecordError):
            repo._to_row({"flag": "maybe"}, for_create=True)

    def test_native_json_types_pass_through(self) -> None:
        # A JSON number/bool already decodes to the right Python type.
        repo = _repo([_field("age", "integer"), _field("flag", "boolean")])
        row = repo._to_row({"age": 7, "flag": True}, for_create=True)
        assert 7 in row.values() and True in row.values()


class TestRequiredNullEnforcement:
    def test_update_nulling_required_scalar_is_400(self) -> None:
        repo = _repo([_field("name", "text", required=True)])
        with pytest.raises(EntityRecordError):
            repo._to_row({"name": None}, for_create=False)

    def test_update_nulling_required_relationship_is_400(self) -> None:
        repo = _repo([_field("ref", "text")], [_rel("owner", required=True)])
        with pytest.raises(EntityRecordError):
            repo._to_row({"owner": None}, for_create=False)

    def test_update_nulling_optional_scalar_is_allowed(self) -> None:
        repo = _repo([_field("nickname", "text", required=False)])
        row = repo._to_row({"nickname": None}, for_create=False)
        assert None in row.values()

    def test_create_missing_required_still_rejected(self) -> None:
        repo = _repo([_field("name", "text", required=True)])
        with pytest.raises(EntityRecordError):
            repo._to_row({}, for_create=True)
