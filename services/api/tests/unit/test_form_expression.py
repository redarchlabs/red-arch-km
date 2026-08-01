"""Unit tests for the sandboxed calculated-field expression evaluator."""

from __future__ import annotations

import pytest
from api.services import form_expression as fx


def test_literals_pass_through():
    assert fx.evaluate(42, {}) == 42
    assert fx.evaluate("hello", {}) == "hello"
    assert fx.evaluate(True, {}) is True
    assert fx.evaluate(None, {}) is None


def test_var_lookup_and_missing():
    assert fx.evaluate({"var": "name"}, {"name": "Ada"}) == "Ada"
    assert fx.evaluate({"var": "missing"}, {"name": "Ada"}) is None
    assert fx.evaluate({"var": "a.b"}, {"a": {"b": 7}}) == 7


def test_arithmetic():
    assert fx.evaluate({"+": [{"var": "a"}, {"*": [{"var": "b"}, 2]}]}, {"a": 10, "b": 5}) == 20
    assert fx.evaluate({"-": [10, 3, 2]}, {}) == 5
    # division by zero degrades to None rather than raising
    assert fx.evaluate({"/": [1, 0]}, {}) is None


def test_string_concat():
    assert fx.evaluate({"cat": [{"var": "f"}, " ", {"var": "l"}]}, {"f": "Ada", "l": "L"}) == "Ada L"
    # None components render as empty string, not "None"
    assert fx.evaluate({"cat": ["x", {"var": "missing"}]}, {}) == "x"


def test_conditionals_and_comparisons():
    assert fx.evaluate({"if": [{">": [{"var": "n"}, 3]}, "big", "small"]}, {"n": 5}) == "big"
    assert fx.evaluate({"if": [{">": [{"var": "n"}, 3]}, "big", "small"]}, {"n": 1}) == "small"
    assert fx.evaluate({"==": [{"var": "n"}, 5]}, {"n": 5}) is True
    # numeric-string coercion in comparisons
    assert fx.evaluate({"<": ["9", 10]}, {}) is True


def test_and_or_not():
    assert fx.evaluate({"and": [True, True]}, {}) is True
    assert fx.evaluate({"and": [True, False]}, {}) is False
    assert fx.evaluate({"or": [False, "fallback"]}, {}) == "fallback"
    assert fx.evaluate({"!": [False]}, {}) is True


def test_today_and_now_are_iso():
    today = fx.evaluate({"today": []}, {})
    assert isinstance(today, str) and len(today) == 10 and today[4] == "-"
    now = fx.evaluate({"now": []}, {})
    assert isinstance(now, str) and "T" in now


@pytest.mark.parametrize(
    "base,amount,unit,expected",
    [
        ("2026-07-07", 30, "day", "2026-08-06"),
        ("2026-07-07", 2, "week", "2026-07-21"),
        ("2026-01-31", 1, "month", "2026-02-28"),  # clamps to month end
        ("2026-07-07", 1, "year", "2027-07-07"),
    ],
)
def test_date_add(base, amount, unit, expected):
    assert fx.evaluate({"date_add": [base, amount, unit]}, {}) == expected


def test_date_diff():
    assert fx.evaluate({"date_diff": ["2026-08-01", "2026-07-07"]}, {}) == 25
    assert fx.evaluate({"date_diff": ["2026-07-07", "2026-08-01"]}, {}) == -25


def test_bad_formula_degrades_to_none():
    assert fx.evaluate({"unknown_op": [1, 2]}, {}) is None
    assert fx.evaluate({"date_add": ["not-a-date", 1, "day"]}, {}) is None


def test_truthy_cast_matches_the_client():
    """`!!` is how an author asks "is this set?" — a view gates the crew station's
    challenge on it. The client evaluator has it, so this one must too, or the same
    expression means different things on the two sides."""
    assert fx.evaluate({"!!": [{"var": "rel"}]}, {"rel": "abc"}) is True
    assert fx.evaluate({"!!": [{"var": "rel"}]}, {"rel": None}) is False
    assert fx.evaluate({"!!": [{"var": "rel"}]}, {}) is False
    assert fx.evaluate({"!!": [{"var": "rows"}]}, {"rows": []}) is False


def test_strict_equality_does_not_coerce():
    """`===`/`!==` are the strict forms: unlike `==` they never coerce a numeric
    string, which is the whole reason an author reaches for them."""
    assert fx.evaluate({"==": ["5", 5]}, {}) is True
    assert fx.evaluate({"===": ["5", 5]}, {}) is False
    assert fx.evaluate({"===": [5, 5]}, {}) is True
    assert fx.evaluate({"!==": [{"var": "status"}, "complete"]}, {"status": "active"}) is True
    assert fx.evaluate({"!==": [{"var": "status"}, "complete"]}, {"status": "complete"}) is False


def test_strict_equality_keeps_booleans_apart_from_numbers():
    """A bool is not 1 under strict equality, matching JavaScript."""
    assert fx.evaluate({"===": [True, 1]}, {}) is False
    assert fx.evaluate({"===": [True, True]}, {}) is True


def test_membership_covers_lists_and_substrings():
    """`in` matches an element of a list or a substring of a string; a missing
    container is never a match rather than an error."""
    assert fx.evaluate({"in": ["b", ["a", "b"]]}, {}) is True
    assert fx.evaluate({"in": ["c", ["a", "b"]]}, {}) is False
    assert fx.evaluate({"in": ["ell", "hello"]}, {}) is True
    assert fx.evaluate({"in": ["x", {"var": "missing"}]}, {}) is False
