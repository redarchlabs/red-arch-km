"""Unit tests for semantic workflow-graph validation (services/workflow/validation.py).

Pure graph analysis — no DB. Each test targets one rule; a helper builds minimal
graphs so a single warning/error is asserted in isolation.
"""

from __future__ import annotations

from api.services.workflow import validation
from api.services.workflow.validation import validate_definition


def _codes(defn: dict) -> set[str]:
    return {i.code for i in validate_definition(defn)}


def test_clean_linear_graph_has_no_issues() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "t1", "type": "task", "data": {"task_type": "service"}},
            {"id": "end1", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"source": "start", "target": "t1"},
            {"source": "t1", "target": "end1"},
        ],
    }
    assert validate_definition(defn) == []


def test_missing_trigger_is_error() -> None:
    defn = {"schema_version": 2, "nodes": [{"id": "t1", "type": "task", "data": {"task_type": "service"}}]}
    issues = validate_definition(defn)
    assert any(i.code == "no-trigger" and i.severity == "error" for i in issues)
    assert validation.has_errors(issues) is True


def test_multiple_triggers_warns() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "s1", "type": "trigger", "data": {}},
            {"id": "s2", "type": "trigger", "data": {}},
        ],
    }
    assert "multiple-triggers" in _codes(defn)


def test_unreachable_node_warns() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "island", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [],
    }
    issues = validate_definition(defn)
    assert any(i.code == "unreachable" and i.node_id == "island" for i in issues)


def test_boundary_reachable_via_host_not_unreachable() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "a1", "type": "task", "data": {"task_type": "service"}},
            {"id": "b1", "type": "event", "data": {"position": "boundary", "event_type": "timer", "attached_to": "a1"}},
        ],
        "edges": [{"source": "start", "target": "a1"}],
    }
    assert "unreachable" not in _codes(defn)


def test_boundary_unattached_is_error() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "b1", "type": "event", "data": {"position": "boundary", "event_type": "error"}},
        ],
    }
    assert "boundary-unattached" in _codes(defn)


def test_boundary_attached_to_unknown_is_error() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "b1", "type": "event", "data": {"position": "boundary", "event_type": "error", "attached_to": "ghost"}},
        ],
    }
    assert "boundary-bad-attach" in _codes(defn)


def test_boundary_on_non_task_warns() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive"}},
            {"id": "b1", "type": "event", "data": {"position": "boundary", "event_type": "timer", "attached_to": "gw"}},
        ],
        "edges": [{"source": "start", "target": "gw"}],
    }
    assert "boundary-nonactivity" in _codes(defn)


def test_exclusive_gateway_without_default_warns() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive", "expr": {"var": "after.x"}}},
            {"id": "a", "type": "task", "data": {"task_type": "service"}},
            {"id": "b", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [
            {"source": "start", "target": "gw"},
            {"source": "gw", "target": "a", "source_handle": "true"},
            {"source": "gw", "target": "b", "source_handle": "false"},
        ],
    }
    assert "exclusive-no-default" in _codes(defn)


def test_exclusive_gateway_with_default_ok() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive", "expr": {"var": "after.x"}}},
            {"id": "a", "type": "task", "data": {"task_type": "service"}},
            {"id": "b", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [
            {"source": "start", "target": "gw"},
            {"source": "gw", "target": "a", "source_handle": "true"},
            {"source": "gw", "target": "b", "source_handle": "default"},
        ],
    }
    assert "exclusive-no-default" not in _codes(defn)


def test_parallel_gateway_single_branch_warns() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "parallel"}},
            {"id": "a", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [
            {"source": "start", "target": "gw"},
            {"source": "gw", "target": "a"},
        ],
    }
    assert "degenerate-gateway" in _codes(defn)


def test_parallel_fork_join_clean() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "fork", "type": "gateway", "data": {"gateway_type": "parallel"}},
            {"id": "a", "type": "task", "data": {"task_type": "service"}},
            {"id": "b", "type": "task", "data": {"task_type": "service"}},
            {"id": "join", "type": "gateway", "data": {"gateway_type": "parallel"}},
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"source": "start", "target": "fork"},
            {"source": "fork", "target": "a"},
            {"source": "fork", "target": "b"},
            {"source": "a", "target": "join"},
            {"source": "b", "target": "join"},
            {"source": "join", "target": "end"},
        ],
    }
    assert validate_definition(defn) == []


def test_event_based_gateway_to_task_is_error() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "event_based"}},
            {"id": "t", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [
            {"source": "start", "target": "gw"},
            {"source": "gw", "target": "t"},
        ],
    }
    assert "event-gateway-target" in _codes(defn)


def test_event_based_gateway_to_catch_ok() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "event_based"}},
            {"id": "timer", "type": "event", "data": {"position": "intermediate", "event_type": "timer", "throw_catch": "catch"}},
            {"id": "recv", "type": "task", "data": {"task_type": "receive"}},
        ],
        "edges": [
            {"source": "start", "target": "gw"},
            {"source": "gw", "target": "timer"},
            {"source": "gw", "target": "recv"},
        ],
    }
    assert "event-gateway-target" not in _codes(defn)


def test_progressless_loop_warns() -> None:
    """A loop of only gateways can spin without progress."""
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "g1", "type": "gateway", "data": {"gateway_type": "exclusive"}},
            {"id": "g2", "type": "gateway", "data": {"gateway_type": "exclusive"}},
        ],
        "edges": [
            {"source": "start", "target": "g1"},
            {"source": "g1", "target": "g2"},
            {"source": "g2", "target": "g1"},
        ],
    }
    assert "loop-no-progress" in _codes(defn)


def test_loop_with_task_is_allowed() -> None:
    """A bounded loop that does work each iteration is fine (no warning)."""
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "exclusive", "expr": {"var": "vars.done"}, "default_handle": "loop"}},
            {"id": "work", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [
            {"source": "start", "target": "gw"},
            {"source": "gw", "target": "work", "source_handle": "default"},
            {"source": "work", "target": "gw"},
        ],
    }
    assert "loop-no-progress" not in _codes(defn)


def test_v2_without_end_event_warns() -> None:
    defn = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "t", "type": "task", "data": {"task_type": "service"}},
        ],
        "edges": [{"source": "start", "target": "t"}],
    }
    assert "no-end-event" in _codes(defn)


def test_legacy_graph_not_penalized_for_missing_end() -> None:
    """A legacy (v1) graph shouldn't get the v2-only no-end-event warning."""
    defn = {
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {"operations": ["update"]}},
            {"id": "a", "type": "action", "data": {"action_type": "log"}},
        ],
        "edges": [{"source": "start", "target": "a"}],
    }
    assert "no-end-event" not in _codes(defn)


def test_malformed_definition_returns_error_issue() -> None:
    issues = validate_definition({"nodes": [{"id": "bad id", "type": "task"}]})
    assert validation.has_errors(issues)
    assert any(i.code == "malformed" for i in issues)
