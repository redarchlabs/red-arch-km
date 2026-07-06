"""Workflow graph evaluation.

Shared by the dispatcher (production) and the dry-run test endpoint so the two
paths cannot diverge. Walks the node graph from the trigger, evaluating
``condition`` nodes (JsonLogic) to choose the ``true``/``false`` edge and
collecting the ordered ``action`` nodes on the taken path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from api.services.workflow.jsonlogic import json_logic


@dataclass
class ConditionTraceEntry:
    node_id: str
    result: bool


@dataclass
class GraphEvaluation:
    matched: bool
    actions: list[dict[str, Any]] = field(default_factory=list)
    trace: list[ConditionTraceEntry] = field(default_factory=list)
    error: str | None = None


def trigger_matches(trigger_data: dict[str, Any], operation: str, changed_fields: set[str]) -> bool:
    """Cheap pre-filter: does this operation + changed-field set fire the trigger?"""
    ops = trigger_data.get("operations") or ["create", "update", "delete"]
    if operation not in ops:
        return False
    field_filter = trigger_data.get("field_filter") or []
    if field_filter and operation == "update":
        return bool(set(field_filter) & changed_fields)
    return True


def evaluate_graph(definition: dict[str, Any], context: dict[str, Any]) -> GraphEvaluation:
    """Traverse the graph from the trigger and return the actions to run.

    ``context`` is ``{"before": {...}|None, "after": {...}|None}``. Any error in
    a condition expression is captured (never raised) so a malformed workflow
    fails its run instead of crashing the batch.
    """
    nodes = {n["id"]: n for n in definition.get("nodes", [])}
    adjacency: dict[str, list[tuple[str, str | None]]] = {}
    for edge in definition.get("edges", []):
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge.get("source_handle")))

    trigger = next((n for n in nodes.values() if n.get("type") == "trigger"), None)
    if trigger is None:
        return GraphEvaluation(matched=False, error="no trigger node")

    actions: list[dict[str, Any]] = []
    trace: list[ConditionTraceEntry] = []
    visited: set[str] = set()
    current: str | None = trigger["id"]

    try:
        while current is not None and current not in visited:
            visited.add(current)
            node = nodes[current]
            outs = adjacency.get(current, [])
            node_type = node.get("type")

            if node_type == "condition":
                expr = node.get("data", {}).get("expr")
                result = bool(json_logic(expr, context)) if expr is not None else True
                trace.append(ConditionTraceEntry(node_id=current, result=result))
                wanted = "true" if result else "false"
                current = next((t for (t, h) in outs if (h or "true") == wanted), None)
            elif node_type == "action":
                actions.append(node)
                current = outs[0][0] if outs else None
            else:  # trigger / merge / passthrough
                current = outs[0][0] if outs else None
    except Exception as exc:  # noqa: BLE001 - malformed condition must fail the run, not the batch
        return GraphEvaluation(matched=False, actions=actions, trace=trace, error=str(exc))

    return GraphEvaluation(matched=len(actions) > 0, actions=actions, trace=trace)
