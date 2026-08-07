"""Unit tests for agent-task graph validation (the publish-blocking rules)."""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow.validation import validate_definition

pytestmark = pytest.mark.unit

AGENT_ID = str(uuid.uuid4())


def _graph(*, agent_id: str | None = AGENT_ID, task: str = "Do it", boundary: bool = True) -> dict:
    nodes = [
        {"id": "start", "type": "trigger", "data": {}},
        {
            "id": "a1",
            "type": "task",
            "data": {
                "task_type": "agent",
                "agent_id": agent_id,
                "task": task,
                "output_schema": {"category": {"type": "string"}},
            },
        },
        {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
    ]
    edges = [
        {"id": "e1", "source": "start", "target": "a1"},
        {"id": "e2", "source": "a1", "target": "end"},
    ]
    if boundary:
        nodes.append(
            {
                "id": "berr",
                "type": "event",
                "data": {"position": "boundary", "event_type": "error", "attached_to": "a1"},
            }
        )
        nodes.append({"id": "end2", "type": "event", "data": {"position": "end", "event_type": "none"}})
        edges.append({"id": "e3", "source": "berr", "target": "end2"})
    return {"schema_version": 2, "nodes": nodes, "edges": edges}


def _agent_errors(definition: dict) -> list[str]:
    return [
        i.code for i in validate_definition(definition) if i.severity == "error" and i.code.startswith("agent-task")
    ]


def test_valid_agent_task_graph_is_clean():
    assert _agent_errors(_graph()) == []


def test_missing_agent_id_is_an_error():
    assert "agent-task-no-agent" in _agent_errors(_graph(agent_id=None))


def test_non_uuid_agent_id_is_an_error():
    assert "agent-task-no-agent" in _agent_errors(_graph(agent_id="triage-bot"))


def test_empty_task_prompt_is_an_error():
    assert "agent-task-no-task" in _agent_errors(_graph(task="   "))


def test_missing_error_boundary_is_an_error():
    assert "agent-task-no-escalation" in _agent_errors(_graph(boundary=False))


def test_non_agent_graphs_gain_no_agent_issues():
    plain = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            {"id": "t1", "type": "task", "data": {"task_type": "service", "action_type": "log", "config": {}}},
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "t1"},
            {"id": "e2", "source": "t1", "target": "end"},
        ],
    }
    assert _agent_errors(plain) == []
