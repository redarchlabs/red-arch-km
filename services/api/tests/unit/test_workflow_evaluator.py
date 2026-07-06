"""Unit tests for the JsonLogic evaluator and workflow graph traversal."""

from __future__ import annotations

import pytest
from api.services.workflow.evaluator import evaluate_graph, trigger_matches
from api.services.workflow.jsonlogic import json_logic


class TestJsonLogic:
    def test_var_dotted_path(self) -> None:
        data = {"after": {"status": "open"}}
        assert json_logic({"var": "after.status"}, data) == "open"

    def test_var_missing_returns_default(self) -> None:
        assert json_logic({"var": ["after.nope", "fallback"]}, {"after": {}}) == "fallback"

    def test_equality_and_comparison(self) -> None:
        data = {"after": {"amount": 150}}
        assert json_logic({">": [{"var": "after.amount"}, 100]}, data) is True
        assert json_logic({"==": [{"var": "after.amount"}, 150]}, data) is True
        assert json_logic({"<": [{"var": "after.amount"}, 100]}, data) is False

    def test_and_or_not(self) -> None:
        data = {"after": {"a": 5, "b": "x"}}
        rule = {"and": [{">": [{"var": "after.a"}, 1]}, {"==": [{"var": "after.b"}, "x"]}]}
        assert json_logic(rule, data) is True
        assert json_logic({"!": [{"==": [{"var": "after.b"}, "y"]}]}, data) is True

    def test_in_operator(self) -> None:
        assert json_logic({"in": ["open", ["open", "closed"]]}, {}) is True
        assert json_logic({"in": ["x", ["open", "closed"]]}, {}) is False

    def test_unknown_operator_raises(self) -> None:
        with pytest.raises(ValueError):
            json_logic({"pow": [2, 3]}, {})


class TestTriggerMatches:
    def test_operation_filter(self) -> None:
        assert trigger_matches({"operations": ["create"]}, "create", set())
        assert not trigger_matches({"operations": ["create"]}, "update", set())

    def test_field_filter_on_update(self) -> None:
        data = {"operations": ["update"], "field_filter": ["status"]}
        assert trigger_matches(data, "update", {"status"})
        assert not trigger_matches(data, "update", {"title"})

    def test_empty_field_filter_matches_any_update(self) -> None:
        assert trigger_matches({"operations": ["update"]}, "update", {"anything"})


def _graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


class TestEvaluateGraph:
    def test_linear_trigger_to_action(self) -> None:
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "a", "type": "action", "data": {"action_type": "noop"}},
            ],
            [{"id": "e1", "source": "t", "target": "a"}],
        )
        result = evaluate_graph(g, {"after": {}})
        assert result.matched is True
        assert [n["id"] for n in result.actions] == ["a"]

    def test_condition_true_branch(self) -> None:
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "c", "type": "condition", "data": {"expr": {">": [{"var": "after.amount"}, 100]}}},
                {"id": "yes", "type": "action", "data": {"action_type": "big"}},
                {"id": "no", "type": "action", "data": {"action_type": "small"}},
            ],
            [
                {"id": "e1", "source": "t", "target": "c"},
                {"id": "e2", "source": "c", "target": "yes", "source_handle": "true"},
                {"id": "e3", "source": "c", "target": "no", "source_handle": "false"},
            ],
        )
        big = evaluate_graph(g, {"after": {"amount": 500}})
        assert [n["id"] for n in big.actions] == ["yes"]
        assert big.trace[0].result is True

        small = evaluate_graph(g, {"after": {"amount": 5}})
        assert [n["id"] for n in small.actions] == ["no"]
        assert small.trace[0].result is False

    def test_condition_false_dead_end_no_actions(self) -> None:
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "c", "type": "condition", "data": {"expr": {"==": [{"var": "after.s"}, "go"]}}},
                {"id": "a", "type": "action", "data": {"action_type": "x"}},
            ],
            [
                {"id": "e1", "source": "t", "target": "c"},
                {"id": "e2", "source": "c", "target": "a", "source_handle": "true"},
            ],
        )
        result = evaluate_graph(g, {"after": {"s": "stop"}})
        assert result.matched is False
        assert result.actions == []

    def test_malformed_condition_captured_not_raised(self) -> None:
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "c", "type": "condition", "data": {"expr": {"bogus": [1]}}},
            ],
            [{"id": "e1", "source": "t", "target": "c"}],
        )
        result = evaluate_graph(g, {"after": {}})
        assert result.error is not None
        assert result.matched is False

    def test_missing_trigger(self) -> None:
        result = evaluate_graph(_graph([{"id": "a", "type": "action", "data": {}}], []), {})
        assert result.matched is False
        assert result.error == "no trigger node"

    def test_multi_action_chain_order(self) -> None:
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "a1", "type": "action", "data": {}},
                {"id": "a2", "type": "action", "data": {}},
            ],
            [
                {"id": "e1", "source": "t", "target": "a1"},
                {"id": "e2", "source": "a1", "target": "a2"},
            ],
        )
        result = evaluate_graph(g, {})
        assert [n["id"] for n in result.actions] == ["a1", "a2"]
