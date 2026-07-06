"""Unit tests for the pure reconciliation policy — the truth-making core."""

from __future__ import annotations

from brain_sdk.facts.models import Claim, ObjectType
from brain_sdk.facts.predicates import Cardinality
from brain_sdk.facts.reconciliation import ReconcileAction, reconcile


def _claim(obj: str, *, recorded_at: str, valid_from: str | None = None, predicate: str = "headquartered_in") -> Claim:
    return Claim(
        tenant_id="t1",
        subject_id="s1",
        predicate=predicate,
        object_type=ObjectType.TEXT,
        object_value=obj,
        valid_from=valid_from,
        recorded_at=recorded_at,
    )


class TestReconcile:
    def test_same_fact_corroborates(self) -> None:
        new = _claim("Paris", recorded_at="2024-02-01T00:00:00+00:00")
        result = reconcile(
            new,
            cardinality=Cardinality.FUNCTIONAL,
            existing_same=new,  # identical dedup key present
            active_conflicts=(),
        )
        assert result.action is ReconcileAction.CORROBORATE

    def test_multi_predicate_always_creates(self) -> None:
        new = _claim("Book A", recorded_at="2024-02-01T00:00:00+00:00", predicate="authored")
        conflict = _claim("Book B", recorded_at="2024-01-01T00:00:00+00:00", predicate="authored")
        result = reconcile(
            new,
            cardinality=Cardinality.MULTI,
            existing_same=None,
            active_conflicts=(conflict,),  # ignored for MULTI
        )
        assert result.action is ReconcileAction.CREATE

    def test_functional_no_conflict_creates(self) -> None:
        new = _claim("Paris", recorded_at="2024-02-01T00:00:00+00:00")
        result = reconcile(new, cardinality=Cardinality.FUNCTIONAL, existing_same=None, active_conflicts=())
        assert result.action is ReconcileAction.CREATE

    def test_functional_newer_supersedes(self) -> None:
        old = _claim("London", recorded_at="2020-01-01T00:00:00+00:00")
        new = _claim("Paris", recorded_at="2024-01-01T00:00:00+00:00")
        result = reconcile(new, cardinality=Cardinality.FUNCTIONAL, existing_same=None, active_conflicts=(old,))
        assert result.action is ReconcileAction.SUPERSEDE
        assert result.affected_keys == (old.dedup_key,)

    def test_functional_prefers_valid_from_over_recorded_at(self) -> None:
        # New has an OLDER recorded_at but a clearly LATER world-time (valid_from),
        # so it should still supersede on world-time ordering.
        old = _claim("London", recorded_at="2024-06-01T00:00:00+00:00", valid_from="2010-01-01T00:00:00+00:00")
        new = _claim("Paris", recorded_at="2024-01-01T00:00:00+00:00", valid_from="2020-01-01T00:00:00+00:00")
        result = reconcile(new, cardinality=Cardinality.FUNCTIONAL, existing_same=None, active_conflicts=(old,))
        assert result.action is ReconcileAction.SUPERSEDE

    def test_functional_unorderable_contradicts(self) -> None:
        # Same recorded_at, no world-time → cannot order → contradiction.
        old = _claim("London", recorded_at="2024-01-01T00:00:00+00:00")
        new = _claim("Paris", recorded_at="2024-01-01T00:00:00+00:00")
        result = reconcile(new, cardinality=Cardinality.FUNCTIONAL, existing_same=None, active_conflicts=(old,))
        assert result.action is ReconcileAction.CONTRADICT
        assert result.new_status.value == "contradicted"
        assert result.affected_keys == (old.dedup_key,)

    def test_functional_mixed_flags_only_unorderable(self) -> None:
        orderable = _claim("London", recorded_at="2020-01-01T00:00:00+00:00")
        unorderable = _claim("Berlin", recorded_at="2024-01-01T00:00:00+00:00")
        new = _claim("Paris", recorded_at="2024-01-01T00:00:00+00:00")
        result = reconcile(
            new,
            cardinality=Cardinality.FUNCTIONAL,
            existing_same=None,
            active_conflicts=(orderable, unorderable),
        )
        assert result.action is ReconcileAction.CONTRADICT
        # Only the value we couldn't order past is flagged.
        assert result.affected_keys == (unorderable.dedup_key,)
