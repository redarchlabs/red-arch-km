"""Unit tests for the entity/field access-control policy that makes a record
surface tamper-proof (e.g. a quiz answer key + a certification record).

These exercise ``DynamicEntityRepository``'s pure transforms (``_to_public`` read
strip, ``_to_row`` write strip, ``_column`` filter/sort guard, the ``search``
column set, and the ``_guard_writable`` write gate) with no database — the repo
builds its SQLAlchemy ``Table`` from the catalog in ``__init__`` but these paths
don't touch the session. The invariant under test: a NON-privileged caller (a
regular member) cannot read a ``server_only`` field or write a ``workflow_only``
entity, while a PRIVILEGED caller (the workflow engine / an org admin) can.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from api.repositories.dynamic_entity import (
    DynamicEntityRepository,
    EntityRecordError,
    RecordAccessError,
)
from api.services import identifiers


def _field(slug: str, field_type: str = "text", *, read_access: str = "member"):  # type: ignore[no-untyped-def]
    f = MagicMock()
    f.slug = slug
    f.field_type = field_type
    f.is_required = False
    f.is_unique = False
    f.picklist_options = []
    f.read_access = read_access
    f.physical_column = identifiers.column_name(uuid.uuid4())
    return f


def _repo(fields, *, privileged: bool = False, write_access: str = "member"):  # type: ignore[no-untyped-def]
    definition = MagicMock()
    definition.physical_table = identifiers.table_name(uuid.uuid4())
    definition.id = uuid.uuid4()
    definition.slug = "certification"
    definition.write_access = write_access
    return DynamicEntityRepository(
        MagicMock(), uuid.uuid4(), definition, fields, [], privileged=privileged
    )


def _phys_row(repo, **values):  # type: ignore[no-untyped-def]
    """Build a physical-column-keyed DB row (what ``_to_public`` receives) from
    slug=value pairs, using the repo's slug→column map."""
    row: dict = {"id": uuid.uuid4()}
    for slug, value in values.items():
        row[repo._slug_to_col[slug]] = value
    return row


class TestServerOnlyReadStrip:
    """A ``server_only`` field is hidden from a non-privileged read but visible to
    a privileged one — the single ``_to_public`` chokepoint every read funnels
    through, so it can't leak from any route."""

    def test_member_read_omits_server_only_field(self) -> None:
        repo = _repo([_field("prompt"), _field("correct_answer", read_access="server_only")])
        out = repo._to_public(_phys_row(repo, prompt="2+2?", correct_answer="4"))
        assert "prompt" in out
        assert "correct_answer" not in out  # answer key hidden

    def test_privileged_read_includes_server_only_field(self) -> None:
        repo = _repo(
            [_field("prompt"), _field("correct_answer", read_access="server_only")],
            privileged=True,
        )
        out = repo._to_public(_phys_row(repo, prompt="2+2?", correct_answer="4"))
        assert out["correct_answer"] == "4"  # the grading workflow can read it

    def test_member_read_unaffected_when_no_server_only_fields(self) -> None:
        repo = _repo([_field("prompt"), _field("correct_answer")])
        out = repo._to_public(_phys_row(repo, prompt="2+2?", correct_answer="4"))
        assert out["correct_answer"] == "4"  # default policy is fully open


class TestServerOnlyWriteStrip:
    """A non-privileged caller can't SET a server-only field (defence-in-depth so a
    member can't overwrite the answer key on a member-writable entity)."""

    def test_member_write_drops_server_only_field(self) -> None:
        repo = _repo([_field("prompt"), _field("correct_answer", read_access="server_only")])
        row = repo._to_row({"prompt": "hi", "correct_answer": "hacked"}, for_create=False)
        cols = set(row)
        assert repo._slug_to_col["prompt"] in cols
        assert repo._slug_to_col["correct_answer"] not in cols  # silently ignored

    def test_privileged_write_keeps_server_only_field(self) -> None:
        repo = _repo(
            [_field("prompt"), _field("correct_answer", read_access="server_only")],
            privileged=True,
        )
        row = repo._to_row({"correct_answer": "4"}, for_create=False)
        assert repo._slug_to_col["correct_answer"] in row


class TestServerOnlyFilterGuard:
    """A server-only field can't be filtered/sorted/grouped by a non-privileged
    caller — otherwise its values leak via a filter oracle or an aggregate group_by."""

    def test_member_column_ref_on_server_only_raises(self) -> None:
        repo = _repo([_field("correct_answer", read_access="server_only")])
        with pytest.raises(EntityRecordError):
            repo._column("correct_answer")

    def test_member_filter_condition_on_server_only_raises(self) -> None:
        repo = _repo([_field("correct_answer", read_access="server_only")])
        with pytest.raises(EntityRecordError):
            repo._filter_condition("correct_answer", "eq", "4")

    def test_privileged_column_ref_on_server_only_ok(self) -> None:
        repo = _repo([_field("correct_answer", read_access="server_only")], privileged=True)
        assert repo._column("correct_answer") is not None

    def test_member_can_still_reference_open_fields(self) -> None:
        repo = _repo([_field("prompt"), _field("correct_answer", read_access="server_only")])
        assert repo._column("prompt") is not None  # non-protected field unaffected


class TestWorkflowOnlyWriteGate:
    """A ``workflow_only`` entity rejects direct member writes (403 →
    ``RecordAccessError``) but the workflow engine / admin (privileged) may write."""

    def test_member_guard_raises_for_workflow_only(self) -> None:
        repo = _repo([_field("passed", "boolean")], write_access="workflow_only")
        with pytest.raises(RecordAccessError):
            repo._guard_writable()

    def test_privileged_guard_allows_workflow_only(self) -> None:
        repo = _repo([_field("passed", "boolean")], write_access="workflow_only", privileged=True)
        repo._guard_writable()  # no raise — the grader may issue the certificate

    def test_member_guard_allows_default_member_entity(self) -> None:
        repo = _repo([_field("passed", "boolean")], write_access="member")
        repo._guard_writable()  # default entity is member-writable

    def test_record_access_error_is_not_a_record_error(self) -> None:
        # It must NOT be caught by the routers' `except EntityRecordError -> 400`;
        # it maps to a dedicated 403 handler instead.
        assert not issubclass(RecordAccessError, EntityRecordError)


class TestDefaultsAreOpen:
    """With no policy set, behaviour is the pre-existing fully-open one."""

    def test_no_server_only_no_strip_and_no_gate(self) -> None:
        repo = _repo([_field("a"), _field("b")])
        assert repo._server_only_slugs == set()
        out = repo._to_public(_phys_row(repo, a=1, b=2))
        assert out["a"] == 1 and out["b"] == 2
        repo._guard_writable()  # member entity, no raise
