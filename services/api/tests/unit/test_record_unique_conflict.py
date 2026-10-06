"""Unit tests: a unique violation on a record write becomes ``RecordConflictError``.

The record table's unique constraints (a unique field, a one-to-one relationship)
used to surface as an unhandled ``IntegrityError`` — a 500 for the caller and an
aborted transaction for anything sharing the session. The repository now runs each
write statement in a savepoint and translates a unique violation into a
``RecordConflictError`` naming the field. The session is mocked here; the
integration suite proves the same against PostgreSQL.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError, RecordConflictError
from api.services import identifiers
from sqlalchemy.exc import IntegrityError


class _Savepoint:
    """Stands in for ``session.begin_nested()``; records whether it was rolled back."""

    def __init__(self) -> None:
        self.rolled_back = False

    async def __aenter__(self) -> _Savepoint:
        return self

    async def __aexit__(self, exc_type: Any, *_rest: Any) -> bool:
        self.rolled_back = exc_type is not None
        return False


class _DriverError(Exception):
    """The asyncpg error SQLAlchemy chains as ``orig.__cause__``."""

    def __init__(self, sqlstate: str, constraint_name: str | None) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity_error(sqlstate: str, constraint_name: str | None) -> IntegrityError:
    """An ``IntegrityError`` shaped like SQLAlchemy's asyncpg translation."""
    driver = _DriverError(sqlstate, constraint_name)
    orig = Exception("<class 'asyncpg.exceptions.UniqueViolationError'>: Key (org_id, f_x)=(..., 'A-1') exists")
    orig.sqlstate = sqlstate  # type: ignore[attr-defined]
    orig.__cause__ = driver
    return IntegrityError("INSERT ...", {}, orig)


def _field(slug: str, *, unique: bool = False) -> MagicMock:
    f = MagicMock()
    f.id = uuid.uuid4()
    f.slug = slug
    f.field_type = "text"
    f.is_required = False
    f.is_unique = unique
    f.read_access = "member"
    f.picklist_options = []
    f.physical_column = identifiers.column_name(f.id)
    return f


def _one_to_one(slug: str) -> MagicMock:
    r = MagicMock()
    r.id = uuid.uuid4()
    r.slug = slug
    r.is_required = False
    r.cardinality = "one_to_one"
    r.physical_name = identifiers.relation_column_name(r.id)
    return r


def _repo(fields: list[MagicMock], rels: list[MagicMock] | None = None) -> tuple[DynamicEntityRepository, MagicMock]:
    session = MagicMock()
    session.savepoints = []

    def _begin_nested() -> _Savepoint:
        sp = _Savepoint()
        session.savepoints.append(sp)
        return sp

    session.begin_nested = MagicMock(side_effect=_begin_nested)
    definition = MagicMock()
    definition.id = uuid.uuid4()
    definition.slug = "asset"
    definition.write_access = "member"
    definition.physical_table = identifiers.table_name(definition.id)
    repo = DynamicEntityRepository(session, uuid.uuid4(), definition, fields, rels or [], privileged=True)
    return repo, session


class TestCreateConflict:
    async def test_unique_field_violation_names_the_field(self) -> None:
        code = _field("code", unique=True)
        repo, session = _repo([code, _field("name")])
        session.execute = AsyncMock(side_effect=_integrity_error("23505", identifiers.unique_constraint_name(code.id)))

        with pytest.raises(RecordConflictError) as exc:
            await repo.create({"code": "A-1", "name": "x"})

        assert exc.value.fields == ("code",)
        assert "'code'" in str(exc.value)
        # Never echo the conflicting value (it belongs to another row).
        assert "A-1" not in str(exc.value)
        assert session.savepoints[-1].rolled_back is True

    async def test_one_to_one_relationship_violation_names_the_relationship(self) -> None:
        owner = _one_to_one("owner")
        repo, session = _repo([_field("name")], [owner])
        repo._validate_relationships = AsyncMock()  # type: ignore[method-assign]
        session.execute = AsyncMock(side_effect=_integrity_error("23505", identifiers.unique_constraint_name(owner.id)))

        with pytest.raises(RecordConflictError) as exc:
            await repo.create({"owner": str(uuid.uuid4())})

        assert exc.value.fields == ("owner",)

    async def test_unknown_unique_constraint_is_a_generic_conflict(self) -> None:
        repo, session = _repo([_field("name")])
        session.execute = AsyncMock(side_effect=_integrity_error("23505", "some_other_constraint"))

        with pytest.raises(RecordConflictError) as exc:
            await repo.create({"name": "x"})

        assert exc.value.fields == ()
        assert "some_other_constraint" not in str(exc.value)

    async def test_conflict_is_an_entity_record_error(self) -> None:
        """Callers that already turn a bad payload into a clean error (agent tools,
        the importer, forms) handle a conflict the same way without changes."""
        assert issubclass(RecordConflictError, EntityRecordError)

    async def test_non_unique_integrity_error_is_not_swallowed(self) -> None:
        repo, session = _repo([_field("name")])
        session.execute = AsyncMock(side_effect=_integrity_error("23503", "fk_something"))

        with pytest.raises(IntegrityError):
            await repo.create({"name": "x"})
        assert session.savepoints[-1].rolled_back is True


class TestUpdateConflict:
    async def test_unique_field_violation_on_update(self) -> None:
        code = _field("code", unique=True)
        repo, session = _repo([code])
        session.execute = AsyncMock(side_effect=_integrity_error("23505", identifiers.unique_constraint_name(code.id)))

        with pytest.raises(RecordConflictError) as exc:
            await repo.update(uuid.uuid4(), {"code": "A-1"})

        assert exc.value.fields == ("code",)
        assert session.savepoints[-1].rolled_back is True

    async def test_unique_field_violation_on_increment_literal(self) -> None:
        code = _field("code", unique=True)
        repo, session = _repo([code])
        session.execute = AsyncMock(side_effect=_integrity_error("23505", identifiers.unique_constraint_name(code.id)))

        with pytest.raises(RecordConflictError):
            await repo.increment(uuid.uuid4(), {}, values={"code": "A-1"})
