"""Unit tests for record-list server-side filtering: query-param parsing, the
cursor codec, and the composite keyset predicate — all without a database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError, RecordCursor
from api.routers.entity_records import _decode_cursor, _encode_cursor, _parse_filters
from api.services import identifiers
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql


def _field(slug: str, field_type: str):  # type: ignore[no-untyped-def]
    f = MagicMock()
    f.slug = slug
    f.field_type = field_type
    f.is_required = False
    f.is_unique = False
    f.picklist_options = []
    f.physical_column = identifiers.column_name(uuid.uuid4())
    return f


def _repo(fields):  # type: ignore[no-untyped-def]
    definition = MagicMock()
    definition.physical_table = identifiers.table_name(uuid.uuid4())
    definition.id = uuid.uuid4()
    return DynamicEntityRepository(MagicMock(), uuid.uuid4(), definition, fields, [])


class TestParseFilters:
    def test_basic_clauses(self) -> None:
        clauses = _parse_filters(["stage:eq:won", "amount:gte:50000"])
        assert clauses == [("stage", "eq", "won"), ("amount", "gte", "50000")]

    def test_in_splits_on_comma(self) -> None:
        assert _parse_filters(["tags:in:a,b,c"]) == [("tags", "in", ["a", "b", "c"])]

    def test_isnull_reads_boolean(self) -> None:
        assert _parse_filters(["email:isnull"]) == [("email", "isnull", True)]
        assert _parse_filters(["email:isnull:false"]) == [("email", "isnull", False)]

    def test_value_may_contain_colons(self) -> None:
        # a timestamp value keeps its colons (split has maxsplit=2)
        assert _parse_filters(["created_at:gte:2026-01-01T00:00:00"]) == [
            ("created_at", "gte", "2026-01-01T00:00:00")
        ]

    def test_unknown_operator_is_400(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _parse_filters(["stage:like:won"])
        assert exc.value.status_code == 400

    def test_malformed_clause_is_400(self) -> None:
        with pytest.raises(HTTPException):
            _parse_filters(["justfield"])
        with pytest.raises(HTTPException):
            _parse_filters([":eq:x"])


class TestCursorCodec:
    def test_roundtrip_timestamp(self) -> None:
        c = RecordCursor("created_at", "desc", datetime(2026, 7, 10, tzinfo=UTC), uuid.uuid4())
        back = _decode_cursor(_encode_cursor(c))
        assert back.order_slug == "created_at"
        assert back.order_dir == "desc"
        assert back.id == c.id

    def test_roundtrip_numeric_and_null(self) -> None:
        for val in (12345, None, "won"):
            c = RecordCursor("amount", "asc", val, uuid.uuid4())
            back = _decode_cursor(_encode_cursor(c))
            assert back.order_value == val

    def test_bad_cursor_is_400(self) -> None:
        with pytest.raises(HTTPException):
            _decode_cursor("not-base64!!")


class TestKeysetPredicate:
    def test_descending_predicate_shape(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        col = repo._column("created_at")
        cond = repo._keyset_after(col, datetime(2026, 7, 10, tzinfo=UTC), uuid.uuid4(), descending=True)
        sql = str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "<" in sql  # strictly-older comparison

    def test_null_cursor_value_only_matches_nulls(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        col = repo._column("amount")
        cond = repo._keyset_after(col, None, uuid.uuid4(), descending=True)
        sql = str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "IS NULL" in sql

    def test_coerce_by_slug_relationship_uuid(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        # created_at base column coerces a string to a tz-aware datetime
        dt = repo._coerce_by_slug("created_at", "2026-07-10T00:00:00")
        assert dt.tzinfo is not None

    def test_coerce_by_slug_bad_datetime_raises(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        with pytest.raises(EntityRecordError):
            repo._coerce_by_slug("created_at", "not-a-date")


class TestFilterNormalization:
    """Regression tests: the report (FilterSpec) path sends isnull with no/empty
    value and `in` as a raw comma string; both must normalize like the query path."""

    def _sql(self, cond) -> str:  # type: ignore[no-untyped-def]
        return str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def test_isnull_empty_value_means_is_null(self) -> None:
        repo = _repo([_field("email", "text")])
        assert "IS NULL" in self._sql(repo._filter_condition("email", "isnull", ""))
        assert "IS NULL" in self._sql(repo._filter_condition("email", "isnull", None))
        assert "IS NULL" in self._sql(repo._filter_condition("email", "isnull", True))

    def test_isnull_false_means_is_not_null(self) -> None:
        repo = _repo([_field("email", "text")])
        assert "IS NOT NULL" in self._sql(repo._filter_condition("email", "isnull", False))

    def test_in_splits_comma_string(self) -> None:
        repo = _repo([_field("stage", "picklist")])
        sql = self._sql(repo._filter_condition("stage", "in", "won,open,lost"))
        assert "IN (" in sql
        assert sql.count("'won'") == 1 and "'open'" in sql and "'lost'" in sql

    def test_in_string_empty_matches_nothing(self) -> None:
        repo = _repo([_field("stage", "picklist")])
        assert "false" in self._sql(repo._filter_condition("stage", "in", "")).lower()

    def test_in_over_cap_raises(self) -> None:
        repo = _repo([_field("n", "integer")])
        with pytest.raises(EntityRecordError):
            repo._filter_condition("n", "in", ",".join(str(i) for i in range(300)))
