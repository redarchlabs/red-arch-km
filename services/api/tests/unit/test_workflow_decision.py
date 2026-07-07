"""Unit tests for the decision-table evaluator (pure, jsonlogic-sandboxed)."""

from __future__ import annotations

import pytest
from api.services.workflow.decision import evaluate_decision_table

pytestmark = pytest.mark.unit

_CTX = {"before": None, "after": {"amount": 150, "country": "US"}, "vars": {}}


def _tiers() -> dict:
    return {
        "hit_policy": "first",
        "rules": [
            {"when": {">=": [{"var": "after.amount"}, 100]}, "output": {"tier": "gold"}},
            {"when": {">=": [{"var": "after.amount"}, 50]}, "output": {"tier": "silver"}},
            {"output": {"tier": "bronze"}},  # default/else row
        ],
    }


def test_first_hit_policy_stops_at_first_match() -> None:
    assert evaluate_decision_table(_tiers(), _CTX) == {"tier": "gold"}
    assert evaluate_decision_table(_tiers(), {"after": {"amount": 60}, "vars": {}}) == {"tier": "silver"}
    assert evaluate_decision_table(_tiers(), {"after": {"amount": 10}, "vars": {}}) == {"tier": "bronze"}


def test_default_row_matches_when_no_condition() -> None:
    spec = {"rules": [{"when": {"==": [{"var": "after.country"}, "CA"]}, "output": {"x": 1}}, {"output": {"x": 0}}]}
    assert evaluate_decision_table(spec, _CTX) == {"x": 0}


def test_collect_merges_all_matching_rules() -> None:
    spec = {
        "hit_policy": "collect",
        "rules": [
            {"when": {">=": [{"var": "after.amount"}, 100]}, "output": {"vip": True}},
            {"when": {"==": [{"var": "after.country"}, "US"]}, "output": {"region": "domestic"}},
            {"when": {"==": [{"var": "after.country"}, "CA"]}, "output": {"region": "foreign"}},
        ],
    }
    assert evaluate_decision_table(spec, _CTX) == {"vip": True, "region": "domestic"}


def test_collect_later_rule_wins_on_key_collision() -> None:
    spec = {
        "hit_policy": "collect",
        "rules": [
            {"output": {"tier": "base"}},
            {"when": {">=": [{"var": "after.amount"}, 100]}, "output": {"tier": "gold"}},
        ],
    }
    assert evaluate_decision_table(spec, _CTX) == {"tier": "gold"}


def test_no_match_yields_empty() -> None:
    spec = {"rules": [{"when": {"==": [{"var": "after.country"}, "CA"]}, "output": {"x": 1}}]}
    assert evaluate_decision_table(spec, _CTX) == {}


def test_malformed_specs_are_safe() -> None:
    assert evaluate_decision_table(None, _CTX) == {}
    assert evaluate_decision_table({}, _CTX) == {}
    assert evaluate_decision_table({"rules": "nope"}, _CTX) == {}
    assert evaluate_decision_table({"rules": ["bad", 3, None]}, _CTX) == {}
    # a rule with a non-dict output contributes nothing but doesn't raise
    assert evaluate_decision_table({"rules": [{"output": "x"}]}, _CTX) == {}
