"""Unit tests for read-time legacy-graph normalization (services/workflow/compat.py).

Pure model transformation — no DB. Verifies the 1:1 legacy -> BPMN mapping, that
edges/handles and node data are preserved, that the input is never mutated, and
that already-v2 graphs pass through unchanged.
"""

from __future__ import annotations

from api.schemas.workflow_definition import WorkflowDefinitionModel
from api.services.workflow import compat


def _legacy_graph() -> dict:
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "trigger", "type": "trigger", "data": {"operations": ["update"]}},
            {"id": "c1", "type": "condition", "data": {"expr": {"var": "after.x"}}},
            {"id": "sw1", "type": "switch", "data": {"cases": [{"handle": "a", "expr": {"var": "y"}}]}},
            {"id": "a1", "type": "action", "data": {"action_type": "send_email", "config": {"to": "x"}}},
            {"id": "a2", "type": "action", "data": {"action_type": "log"}},
            {"id": "d1", "type": "delay", "data": {"delay_seconds": 120}},
            {"id": "m1", "type": "merge", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "c1"},
            {"id": "e2", "source": "c1", "target": "a1", "source_handle": "true"},
            {"id": "e3", "source": "c1", "target": "sw1", "source_handle": "false"},
            {"id": "e4", "source": "sw1", "target": "a2", "source_handle": "a"},
            {"id": "e5", "source": "sw1", "target": "d1", "source_handle": "default"},
        ],
    }


def test_action_becomes_task_with_derived_task_type() -> None:
    model = compat.normalize(_legacy_graph())
    send = model.node_by_id("a1")
    log = model.node_by_id("a2")
    assert send.type == "task" and send.task_type == "send"  # send_email -> Send Task
    assert log.type == "task" and log.task_type == "service"  # log -> Service Task
    # action_type is preserved for the handler to dispatch on.
    assert send.data["action_type"] == "send_email"
    assert send.data["config"] == {"to": "x"}


def test_condition_and_switch_become_exclusive_gateways() -> None:
    model = compat.normalize(_legacy_graph())
    cond = model.node_by_id("c1")
    switch = model.node_by_id("sw1")
    assert cond.type == "gateway" and cond.gateway_type == "exclusive"
    assert cond.data["expr"] == {"var": "after.x"}  # branch expr preserved
    assert switch.type == "gateway" and switch.gateway_type == "exclusive"
    assert switch.data["cases"][0]["handle"] == "a"


def test_merge_becomes_exclusive_gateway_not_join() -> None:
    """merge must forward per-token (exclusive), never AND-join (would deadlock)."""
    model = compat.normalize(_legacy_graph())
    merge = model.node_by_id("m1")
    assert merge.type == "gateway" and merge.gateway_type == "exclusive"


def test_delay_becomes_timer_catch_event() -> None:
    model = compat.normalize(_legacy_graph())
    delay = model.node_by_id("d1")
    assert delay.type == "event"
    assert delay.data["position"] == "intermediate"
    assert delay.data["event_type"] == "timer"
    assert delay.data["throw_catch"] == "catch"
    assert delay.data["delay_seconds"] == 120  # timer duration preserved


def test_trigger_unchanged_and_edges_preserved() -> None:
    model = compat.normalize(_legacy_graph())
    assert model.node_by_id("trigger").type == "trigger"
    # Edges and their branch handles round-trip untouched.
    handles = {e.id: e.source_handle for e in model.edges}
    assert handles["e2"] == "true"
    assert handles["e3"] == "false"
    assert handles["e4"] == "a"
    assert handles["e5"] == "default"
    assert len(model.edges) == 5


def test_normalized_graph_is_v2() -> None:
    model = compat.normalize(_legacy_graph())
    assert model.schema_version >= 2
    assert model.is_v2 is True


def test_input_dict_not_mutated() -> None:
    graph = _legacy_graph()
    before = str(graph)
    compat.normalize(graph)
    assert str(graph) == before  # normalize must not touch the caller's dict


def test_already_v2_graph_passes_through() -> None:
    v2 = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "gw", "type": "gateway", "data": {"gateway_type": "parallel"}},
        ],
        "edges": [{"source": "start", "target": "gw"}],
    }
    model = compat.normalize(v2)
    assert model.node_by_id("gw").gateway_type == "parallel"


def test_accepts_already_parsed_model() -> None:
    parsed = WorkflowDefinitionModel.parse(_legacy_graph())
    model = compat.normalize(parsed)
    assert model.node_by_id("a1").type == "task"


def test_trigger_only_legacy_stays_legacy() -> None:
    """A workflow that is just a trigger (no legacy body nodes) is not upgraded."""
    model = compat.normalize({"schema_version": 1, "nodes": [{"id": "t", "type": "trigger", "data": {}}]})
    assert model.schema_version == 1
    assert model.is_v2 is False
