"""Unit tests: an ``update_record`` ``$ref`` that points at nothing must NOT write NULL.

This is a data-loss guard, learned the hard way. A workflow drove a lesson by keeping a
loop cursor on a session record. A child workflow wrote
``{"awaiting_question": {"$ref": "inputs.question_sequence"}}`` back to that same record.
When the input was absent the ``$ref`` resolved to ``None`` and the child silently
overwrote the cursor with NULL — so the parent's counter reset on every pass and the loop
ran forever, spawning eleven child runs in thirty seconds.

The rule: a ``$ref`` whose PATH does not exist means "I have nothing to say about this
field", so the field is left alone. Clearing a field stays possible — write a literal
``null``, which is an author saying so explicitly.
"""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext

pytestmark = pytest.mark.unit


class FakeRepo:
    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = records or []
        self.update_calls: list[tuple[uuid.UUID, dict]] = []

    async def get(self, record_id: uuid.UUID) -> dict | None:
        for r in self.records:
            if str(r.get("id")) == str(record_id):
                return r
        return None

    async def list(self, *, filters=None, search=None, cursor=None, limit=50, order_by=None, order_dir="desc"):
        return list(self.records)[:limit], None

    async def update(self, record_id: uuid.UUID, patch: dict) -> dict:
        self.update_calls.append((record_id, patch))
        return {"id": record_id, **patch}


def _ctx(config, *, repo, inputs=None, vars=None):
    async def _slug(_name: str):
        return repo

    async def _trigger():
        return repo

    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before={},
        after={},
        inputs=inputs or {},
        vars=vars or {},
        config=config,
        trigger_repo=_trigger,
        repo_for_slug=_slug,
    )


def _handler():
    return ACTION_REGISTRY["update_record"]


@pytest.mark.asyncio
async def test_unresolvable_ref_is_skipped_not_nulled() -> None:
    """The exact regression: the input the child expected was never passed."""
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "awaiting_question": 3, "slide_title": "Old"}])
    ctx = _ctx(
        {
            "target_slug": "class_session",
            "record_id": str(rid),
            "values": {
                "awaiting_question": {"$ref": "inputs.question_sequence"},  # absent
                "slide_title": "Discussion",
            },
        },
        repo=repo,
    )
    out = await _handler().execute(ctx)
    _, patch = repo.update_calls[0]
    assert "awaiting_question" not in patch, "an unresolved $ref must not wipe the stored cursor"
    assert patch["slide_title"] == "Discussion"
    # Silent skipping is its own trap — the step output has to say what it dropped.
    assert out["skipped"] == ["awaiting_question"]


@pytest.mark.asyncio
async def test_resolvable_ref_still_writes() -> None:
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "awaiting_question": 3}])
    ctx = _ctx(
        {
            "target_slug": "s",
            "record_id": str(rid),
            "values": {"awaiting_question": {"$ref": "inputs.question_sequence"}},
        },
        repo=repo,
        inputs={"question_sequence": 7},
    )
    await _handler().execute(ctx)
    assert repo.update_calls[0][1] == {"awaiting_question": 7}


@pytest.mark.asyncio
async def test_ref_to_a_stored_null_still_writes_null() -> None:
    """The path EXISTS and holds null — that is a real value, not a missing one."""
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "note": "keep"}])
    ctx = _ctx(
        {"target_slug": "s", "record_id": str(rid), "values": {"note": {"$ref": "vars.q.comment"}}},
        repo=repo,
        vars={"q": {"comment": None}},
    )
    await _handler().execute(ctx)
    assert repo.update_calls[0][1] == {"note": None}


@pytest.mark.asyncio
async def test_literal_null_still_clears_a_field() -> None:
    """Explicit clearing must remain expressible."""
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "note": "old"}])
    ctx = _ctx({"target_slug": "s", "record_id": str(rid), "values": {"note": None}}, repo=repo)
    await _handler().execute(ctx)
    assert repo.update_calls[0][1] == {"note": None}


@pytest.mark.asyncio
async def test_all_values_unresolved_is_a_noop_not_a_wipe() -> None:
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "a": 1, "b": 2}])
    ctx = _ctx(
        {
            "target_slug": "s",
            "record_id": str(rid),
            "values": {"a": {"$ref": "vars.nope.x"}, "b": {"$ref": "inputs.gone"}},
        },
        repo=repo,
    )
    out = await _handler().execute(ctx)
    assert repo.update_calls == [], "nothing resolved — the row must not be touched at all"
    assert out["updated"] is False
    assert sorted(out["skipped"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_increments_still_apply_when_every_value_is_skipped() -> None:
    """A counter bump must survive alongside an unresolved sibling value."""
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "n": 4}])
    ctx = _ctx(
        {
            "target_slug": "s",
            "record_id": str(rid),
            "values": {"label": {"$ref": "inputs.missing"}},
            "increments": {"n": 1},
        },
        repo=repo,
    )
    await _handler().execute(ctx)
    assert repo.update_calls[0][1] == {"n": 5}


@pytest.mark.asyncio
async def test_partial_path_miss_is_treated_as_missing() -> None:
    """``vars.s2`` exists but has no ``awaiting_question`` key."""
    rid = uuid.uuid4()
    repo = FakeRepo([{"id": rid, "cursor": 9}])
    ctx = _ctx(
        {"target_slug": "s", "record_id": str(rid), "values": {"cursor": {"$ref": "vars.s2.awaiting_question"}}},
        repo=repo,
        vars={"s2": {"id": "abc"}},
    )
    await _handler().execute(ctx)
    assert repo.update_calls == []
