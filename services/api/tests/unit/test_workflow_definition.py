"""Unit tests for the schema_version 2 workflow definition contract.

Pure Pydantic — no DB, no settings singleton. Covers the STRUCTURAL guarantees
the API boundary relies on (well-formed ids, known types/subtypes, edges that
reference real nodes) and the legacy-vs-token engine selection (``is_v2``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.workflow_definition import WorkflowDefinitionModel


def _legacy_v1() -> dict:
    """A pre-BPMN graph as the current designer emits it."""
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "trigger", "type": "trigger", "data": {"operations": ["update"]}},
            {"id": "cond1", "type": "condition", "data": {"expr": {"var": "after.x"}}},
            {"id": "sw1", "type": "switch", "data": {"cases": []}},
            {"id": "act1", "type": "action", "data": {"action_type": "log"}},
            {"id": "d1", "type": "delay", "data": {"delay_seconds": 60}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "cond1"},
            {"id": "e2", "source": "cond1", "target": "act1", "source_handle": "true"},
            {"id": "e3", "source": "cond1", "target": "d1", "source_handle": "false"},
        ],
    }


def _bpmn_v2() -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {"event_kind": "change"}},
            {"id": "gw1", "type": "gateway", "data": {"gateway_type": "parallel"}},
            {"id": "t1", "type": "task", "data": {"task_type": "service", "action_type": "log"}},
            {"id": "u1", "type": "task", "data": {"task_type": "user"}},
            {"id": "end1", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"source": "start", "target": "gw1"},
            {"source": "gw1", "target": "t1"},
            {"source": "gw1", "target": "u1"},
            {"source": "t1", "target": "end1"},
        ],
    }


def test_legacy_v1_parses_and_is_not_v2() -> None:
    model = WorkflowDefinitionModel.parse(_legacy_v1())
    assert len(model.nodes) == 5
    # A trigger is shared vocabulary; legacy graphs still run on the walker.
    assert model.is_v2 is False


def test_bpmn_v2_parses_and_is_v2() -> None:
    model = WorkflowDefinitionModel.parse(_bpmn_v2())
    assert model.is_v2 is True
    assert model.node_by_id("gw1").gateway_type == "parallel"
    assert model.node_by_id("t1").task_type == "service"


def test_new_node_type_forces_v2_even_without_version_bump() -> None:
    """A definition adopting BPMN nodes without bumping schema_version still
    routes to the token engine (guards against a stale version field)."""
    defn = _bpmn_v2()
    defn["schema_version"] = 1
    assert WorkflowDefinitionModel.parse(defn).is_v2 is True


def test_empty_definition_is_valid() -> None:
    model = WorkflowDefinitionModel.parse(None)
    assert model.nodes == []
    assert model.is_v2 is False


def test_wait_state_classification() -> None:
    model = WorkflowDefinitionModel.parse(_bpmn_v2())
    assert model.node_by_id("u1").is_wait_state is True  # user task parks a token
    assert model.node_by_id("t1").is_wait_state is False  # service task is a sync job
    assert model.node_by_id("end1").is_wait_state is False


def test_boundary_event_is_wait_state() -> None:
    node = WorkflowDefinitionModel.parse(
        {
            "schema_version": 2,
            "nodes": [
                {"id": "a1", "type": "task", "data": {"task_type": "service"}},
                {
                    "id": "b1",
                    "type": "event",
                    "data": {"position": "boundary", "event_type": "timer", "attached_to": "a1"},
                },
            ],
        }
    ).node_by_id("b1")
    assert node.is_wait_state is True


@pytest.mark.parametrize(
    "bad_id",
    ["has space", "has.dot", "way-too-long" + "x" * 64, "", "emoji😀"],
)
def test_malformed_node_id_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse({"nodes": [{"id": bad_id, "type": "task"}]})


def test_unknown_node_type_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse({"nodes": [{"id": "n1", "type": "wormhole"}]})


def test_unknown_task_type_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse(
            {"nodes": [{"id": "n1", "type": "task", "data": {"task_type": "telepathy"}}]}
        )


def test_unknown_gateway_type_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse(
            {"nodes": [{"id": "n1", "type": "gateway", "data": {"gateway_type": "quantum"}}]}
        )


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse(
            {"nodes": [{"id": "n1", "type": "event", "data": {"position": "end", "event_type": "boom"}}]}
        )


def test_duplicate_node_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse(
            {"nodes": [{"id": "dup", "type": "task"}, {"id": "dup", "type": "gateway"}]}
        )


def test_edge_to_missing_node_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionModel.parse(
            {
                "nodes": [{"id": "a", "type": "trigger"}],
                "edges": [{"source": "a", "target": "ghost"}],
            }
        )


def test_missing_discriminator_allowed() -> None:
    """A task/gateway/event without its subtype key still parses (defaults are
    applied by the UI/engine); only an *unknown* subtype is rejected."""
    model = WorkflowDefinitionModel.parse(
        {"schema_version": 2, "nodes": [{"id": "n1", "type": "task", "data": {}}]}
    )
    assert model.node_by_id("n1").task_type is None


def test_extra_react_flow_fields_ignored() -> None:
    """React Flow may attach layout/selection fields; they must not break parse."""
    model = WorkflowDefinitionModel.parse(
        {
            "nodes": [
                {"id": "n1", "type": "task", "position": {"x": 1, "y": 2, "z": 3}, "selected": True}
            ],
            "edges": [{"id": "e", "source": "n1", "target": "n1", "animated": True}],
        }
    )
    assert model.node_by_id("n1").position.x == 1.0
