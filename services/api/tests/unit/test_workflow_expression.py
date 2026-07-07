"""Unit tests for the script-task transform evaluator (jsonlogic-sandboxed)."""

from __future__ import annotations

import pytest
from api.services.workflow.expression import evaluate_transform

pytestmark = pytest.mark.unit

_CTX = {"before": None, "after": {"amount": 120, "qty": 3}, "vars": {"rate": 2}}


def test_literals_pass_through() -> None:
    assert evaluate_transform({"a": "x", "b": 5, "c": True, "d": None}, _CTX) == {
        "a": "x", "b": 5, "c": True, "d": None,
    }


def test_arithmetic_and_boolean_expressions() -> None:
    mapping = {
        "total": {"*": [{"var": "after.amount"}, {"var": "after.qty"}]},
        "is_vip": {">=": [{"var": "after.amount"}, 100]},
        "scaled": {"*": [{"var": "after.amount"}, {"var": "vars.rate"}]},
    }
    assert evaluate_transform(mapping, _CTX) == {"total": 360, "is_vip": True, "scaled": 240}


def test_mixed_literals_and_expressions() -> None:
    out = evaluate_transform({"kind": "order", "big": {">": [{"var": "after.amount"}, 500]}}, _CTX)
    assert out == {"kind": "order", "big": False}


def test_malformed_mappings_are_safe() -> None:
    assert evaluate_transform(None, _CTX) == {}
    assert evaluate_transform("nope", _CTX) == {}
    assert evaluate_transform([], _CTX) == {}
    # non-string keys are skipped, valid ones kept
    assert evaluate_transform({"ok": 1, 5: "x"}, _CTX) == {"ok": 1}
