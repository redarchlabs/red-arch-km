"""Unit tests for injection-safe identifier derivation (services/identifiers.py)."""

from __future__ import annotations

import uuid

import pytest
from api.services import identifiers


class TestGeneratedNames:
    def test_table_name_is_prefix_plus_hex(self) -> None:
        did = uuid.UUID("0123456789abcdef0123456789abcdef")
        assert identifiers.table_name(did) == "ce_0123456789abcdef0123456789abcdef"

    def test_each_helper_uses_its_prefix(self) -> None:
        oid = uuid.uuid4()
        assert identifiers.table_name(oid).startswith("ce_")
        assert identifiers.join_table_name(oid).startswith("cej_")
        assert identifiers.column_name(oid).startswith("f_")
        assert identifiers.relation_column_name(oid).startswith("r_")
        assert identifiers.fk_constraint_name(oid).startswith("fk_")
        assert identifiers.unique_constraint_name(oid).startswith("uq_")
        assert identifiers.index_name(oid).startswith("ix_")

    def test_generated_names_are_deterministic(self) -> None:
        oid = uuid.uuid4()
        assert identifiers.table_name(oid) == identifiers.table_name(oid)

    def test_generated_names_within_postgres_limit(self) -> None:
        name = identifiers.join_table_name(uuid.uuid4())  # longest prefix
        assert len(name.encode("utf-8")) <= 63

    def test_generated_names_are_recognized(self) -> None:
        assert identifiers.is_generated_identifier(identifiers.table_name(uuid.uuid4()))
        assert identifiers.is_generated_identifier(identifiers.column_name(uuid.uuid4()))

    def test_non_uuid_rejected(self) -> None:
        with pytest.raises(TypeError):
            identifiers.table_name("not-a-uuid")  # type: ignore[arg-type]


class TestSafeIdentifier:
    @pytest.mark.parametrize(
        "name",
        [
            "ce_0123456789abcdef0123456789abcdef",
            "f_ffffffffffffffffffffffffffffffff",
            "id",
            "org_id",
            "created_at",
        ],
    )
    def test_accepts_valid(self, name: str) -> None:
        assert identifiers.safe_identifier(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            'ce_0"; DROP TABLE users;--',
            "ce_" + "g" * 32,  # non-hex char
            "ce_0123",  # too short
            "Robert'); DROP TABLE students;--",
            "",
            "1abc",  # starts with digit
            "a" * 64,  # exceeds byte limit
            "name with spaces",
            "café",  # non-ascii
        ],
    )
    def test_rejects_invalid(self, name: str) -> None:
        with pytest.raises(ValueError):
            identifiers.safe_identifier(name)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError):
            identifiers.safe_identifier(123)  # type: ignore[arg-type]

    def test_is_generated_rejects_native(self) -> None:
        assert not identifiers.is_generated_identifier("org_id")
        assert not identifiers.is_generated_identifier("ce_short")


class TestQuote:
    def test_quote_validates_and_quotes(self) -> None:
        name = identifiers.table_name(uuid.uuid4())
        quoted = identifiers.quote(name)
        assert name in str(quoted)

    def test_quote_rejects_injection(self) -> None:
        with pytest.raises(ValueError):
            identifiers.quote('foo"; DROP TABLE x;--')
