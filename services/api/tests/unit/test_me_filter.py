"""Unit tests for the record-list ``@me`` filter sentinel resolution.

``resolve_me_filters`` swaps a ``@me`` filter value for the caller's own record id
(resolved server-side by email) so a "my rows" board can't be widened past the
caller's own records. Exercised without a database by mocking the relationship
lookup and the own-record resolver.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api.services.entity_records_helpers import ME_FILTER_SENTINEL, resolve_me_filters
from fastapi import HTTPException

_NIL = str(uuid.UUID(int=0))


def _rel(slug: str, target_id: uuid.UUID):  # type: ignore[no-untyped-def]
    r = MagicMock()
    r.slug = slug
    r.target_definition_id = target_id
    return r


def _definition():  # type: ignore[no-untyped-def]
    d = MagicMock()
    d.id = uuid.uuid4()
    return d


def _patch_rels(rels):  # type: ignore[no-untyped-def]
    """Patch EntityRelationshipRepository so list_for_source returns ``rels``."""
    p = patch("api.services.entity_records_helpers.EntityRelationshipRepository")
    RelRepo = p.start()
    RelRepo.return_value.list_for_source = AsyncMock(return_value=rels)
    return p


class TestResolveMeFilters:
    async def test_no_me_value_is_passthrough_without_db(self) -> None:
        # No @me anywhere → the exact clauses come back and the relationship
        # repository is never even constructed (guarded by the any() short-circuit).
        filters = [("stage", "eq", "won"), ("amount", "gte", "50000")]
        with patch("api.services.entity_records_helpers.EntityRelationshipRepository") as RelRepo:
            out = await resolve_me_filters(MagicMock(), uuid.uuid4(), _definition(), filters, "a@b.com")
        assert out == filters
        RelRepo.assert_not_called()

    async def test_me_resolves_to_callers_own_record_id(self) -> None:
        target = uuid.uuid4()
        own = uuid.uuid4()
        p = _patch_rels([_rel("learner", target)])
        try:
            with patch(
                "api.services.self_record.resolve_own_record_id",
                new=AsyncMock(return_value=own),
            ) as resolver:
                out = await resolve_me_filters(
                    MagicMock(), uuid.uuid4(), _definition(), [("learner", "eq", ME_FILTER_SENTINEL)], "me@x.com"
                )
        finally:
            p.stop()
        assert out == [("learner", "eq", str(own))]
        # Resolved against the RELATION's target entity, not the listed entity.
        assert resolver.await_args.args[2] == target

    async def test_me_with_no_matching_owner_fails_closed(self) -> None:
        p = _patch_rels([_rel("learner", uuid.uuid4())])
        try:
            with patch("api.services.self_record.resolve_own_record_id", new=AsyncMock(return_value=None)):
                out = await resolve_me_filters(
                    MagicMock(), uuid.uuid4(), _definition(), [("learner", "eq", ME_FILTER_SENTINEL)], "ghost@x.com"
                )
        finally:
            p.stop()
        # Nil uuid → matches no real row (empty board) rather than leaking all rows.
        assert out == [("learner", "eq", _NIL)]

    async def test_me_with_no_email_fails_closed(self) -> None:
        p = _patch_rels([_rel("learner", uuid.uuid4())])
        try:
            out = await resolve_me_filters(
                MagicMock(), uuid.uuid4(), _definition(), [("learner", "eq", ME_FILTER_SENTINEL)], None
            )
        finally:
            p.stop()
        assert out == [("learner", "eq", _NIL)]

    async def test_me_on_non_relation_field_is_400(self) -> None:
        p = _patch_rels([])  # entity has no relations → 'status' is not one
        try:
            with pytest.raises(HTTPException) as exc:
                await resolve_me_filters(
                    MagicMock(), uuid.uuid4(), _definition(), [("status", "eq", ME_FILTER_SENTINEL)], "me@x.com"
                )
        finally:
            p.stop()
        assert exc.value.status_code == 400

    async def test_me_preserves_other_clauses_and_op(self) -> None:
        target = uuid.uuid4()
        own = uuid.uuid4()
        p = _patch_rels([_rel("learner", target)])
        try:
            with patch("api.services.self_record.resolve_own_record_id", new=AsyncMock(return_value=own)):
                out = await resolve_me_filters(
                    MagicMock(),
                    uuid.uuid4(),
                    _definition(),
                    [("passed", "eq", "true"), ("learner", "ne", ME_FILTER_SENTINEL)],
                    "me@x.com",
                )
        finally:
            p.stop()
        assert out == [("passed", "eq", "true"), ("learner", "ne", str(own))]
