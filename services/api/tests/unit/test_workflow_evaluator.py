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

    def test_absent_operations_matches_any(self) -> None:
        # Legacy definitions without the key fire on every operation.
        assert trigger_matches({}, "delete", set())

    def test_explicit_empty_operations_matches_none(self) -> None:
        # A schedule-only workflow unchecks all change operations.
        assert not trigger_matches({"operations": []}, "update", set())

    def test_form_source_filter(self) -> None:
        data = {"operations": ["update"], "source": "form"}
        assert trigger_matches(data, "update", set(), source="form")
        assert not trigger_matches(data, "update", set(), source="record")

    def test_any_source_matches_form_and_record(self) -> None:
        data = {"operations": ["update"], "source": "any"}
        assert trigger_matches(data, "update", set(), source="form")
        assert trigger_matches(data, "update", set(), source="record")

    def test_manual_source_never_matches_outbox(self) -> None:
        # A manual (BPMN "none") start event is on-demand only — no record/form
        # change fires it, regardless of operation or (absent) operations key.
        data = {"source": "manual", "inputs": [{"key": "x"}]}
        assert not trigger_matches(data, "create", set(), source="record")
        assert not trigger_matches(data, "update", {"title"}, source="record")
        assert not trigger_matches(data, "delete", set(), source="form")


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

    def test_condition_routes_on_manual_inputs(self) -> None:
        # A manual workflow's post-delay/legacy condition must be able to route on
        # the caller-supplied input variables (the context the resume path rebuilds).
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {"source": "manual"}},
                {"id": "c", "type": "condition", "data": {"expr": {"==": [{"var": "inputs.approved"}, True]}}},
                {"id": "yes", "type": "action", "data": {"action_type": "notify"}},
            ],
            [
                {"id": "e1", "source": "t", "target": "c"},
                {"id": "e2", "source": "c", "target": "yes", "source_handle": "true"},
            ],
        )
        approved = evaluate_graph(g, {"before": None, "after": None, "inputs": {"approved": True}})
        assert [n["id"] for n in approved.actions] == ["yes"]
        rejected = evaluate_graph(g, {"before": None, "after": None, "inputs": {"approved": False}})
        assert rejected.actions == []

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


class TestSwitchNode:
    def _graph(self):
        return _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {
                    "id": "s",
                    "type": "switch",
                    "data": {
                        "cases": [
                            {"handle": "hi", "expr": {">": [{"var": "after.n"}, 100]}},
                            {"handle": "mid", "expr": {">": [{"var": "after.n"}, 10]}},
                        ]
                    },
                },
                {"id": "a_hi", "type": "action", "data": {}},
                {"id": "a_mid", "type": "action", "data": {}},
                {"id": "a_def", "type": "action", "data": {}},
            ],
            [
                {"id": "e0", "source": "t", "target": "s"},
                {"id": "e1", "source": "s", "target": "a_hi", "source_handle": "hi"},
                {"id": "e2", "source": "s", "target": "a_mid", "source_handle": "mid"},
                {"id": "e3", "source": "s", "target": "a_def", "source_handle": "default"},
            ],
        )

    def test_first_matching_case_wins(self) -> None:
        result = evaluate_graph(self._graph(), {"after": {"n": 500}})
        assert [n["id"] for n in result.actions] == ["a_hi"]

    def test_second_case(self) -> None:
        result = evaluate_graph(self._graph(), {"after": {"n": 50}})
        assert [n["id"] for n in result.actions] == ["a_mid"]

    def test_default_when_no_case_matches(self) -> None:
        result = evaluate_graph(self._graph(), {"after": {"n": 1}})
        assert [n["id"] for n in result.actions] == ["a_def"]


class TestDelayNode:
    def _graph(self):
        return _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "a1", "type": "action", "data": {"action_type": "first"}},
                {"id": "d", "type": "delay", "data": {"delay_seconds": 3600}},
                {"id": "a2", "type": "action", "data": {"action_type": "second"}},
            ],
            [
                {"id": "e0", "source": "t", "target": "a1"},
                {"id": "e1", "source": "a1", "target": "d"},
                {"id": "e2", "source": "d", "target": "a2"},
            ],
        )

    def test_pauses_at_delay_returning_pre_delay_actions(self) -> None:
        result = evaluate_graph(self._graph(), {})
        assert result.matched is True
        assert result.paused is True
        assert result.delay_seconds == 3600
        assert result.resume_node_id == "a2"
        assert [n["id"] for n in result.actions] == ["a1"]

    def test_resume_from_node_runs_remaining_actions(self) -> None:
        result = evaluate_graph(self._graph(), {}, start_node_id="a2")
        assert result.paused is False
        assert [n["id"] for n in result.actions] == ["a2"]

    def test_delay_first_still_matches(self) -> None:
        g = _graph(
            [
                {"id": "t", "type": "trigger", "data": {}},
                {"id": "d", "type": "delay", "data": {"delay_seconds": 60}},
                {"id": "a", "type": "action", "data": {}},
            ],
            [
                {"id": "e0", "source": "t", "target": "d"},
                {"id": "e1", "source": "d", "target": "a"},
            ],
        )
        result = evaluate_graph(g, {})
        assert result.matched is True
        assert result.actions == []
        assert result.resume_node_id == "a"
