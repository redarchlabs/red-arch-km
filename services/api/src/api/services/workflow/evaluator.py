"""Workflow graph evaluation.

Shared by the dispatcher (production) and the dry-run test endpoint so the two
paths cannot diverge. Walks the node graph from the trigger (or an arbitrary
``start_node_id`` when resuming after a delay), evaluating ``condition`` nodes
(true/false) and ``switch`` nodes (N-way, first-matching case) to choose the
edge to follow, and collecting the ordered ``action`` nodes on the taken path.

A ``delay`` node pauses the walk: the actions collected *before* it are returned
with ``paused=True`` and a ``resume_node_id`` so the dispatcher can suspend the
run and continue from the delay's successor once the wait elapses.
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
    # Set when the walk stopped at a ``delay`` node: the run should be suspended
    # for ``delay_seconds`` and later resumed from ``resume_node_id``.
    paused: bool = False
    resume_node_id: str | None = None
    delay_seconds: int = 0


def trigger_matches(
    trigger_data: dict[str, Any],
    operation: str,
    changed_fields: set[str],
    *,
    source: str = "record",
) -> bool:
    """Cheap pre-filter: does this operation + changed-field set fire the trigger?

    ``source`` is where the change came from — ``"record"`` for an ordinary edit,
    ``"form"`` for a public intake-form submission. A trigger may pin a required
    source (``{"source": "form"}``) to fire *only* on form submissions.
    """
    # An ABSENT operations key means "any operation" (legacy definitions); an
    # explicit empty list means "no change triggers" (schedule-only workflows).
    ops = trigger_data.get("operations")
    if ops is None:
        ops = ["create", "update", "delete"]
    if operation not in ops:
        return False
    required_source = trigger_data.get("source")
    if required_source and required_source != "any" and required_source != source:
        return False
    field_filter = trigger_data.get("field_filter") or []
    if field_filter and operation == "update":
        return bool(set(field_filter) & changed_fields)
    return True


def evaluate_graph(
    definition: dict[str, Any],
    context: dict[str, Any],
    *,
    start_node_id: str | None = None,
) -> GraphEvaluation:
    """Traverse the graph and return the actions to run.

    ``context`` is ``{"before": {...}|None, "after": {...}|None}``. Starts at the
    trigger, or at ``start_node_id`` when resuming a suspended run. Any error in a
    condition/switch expression is captured (never raised) so a malformed workflow
    fails its run instead of crashing the batch.
    """
    nodes = {n["id"]: n for n in definition.get("nodes", [])}
    adjacency: dict[str, list[tuple[str, str | None]]] = {}
    for edge in definition.get("edges", []):
        adjacency.setdefault(edge["source"], []).append((edge["target"], edge.get("source_handle")))

    if start_node_id is not None:
        current: str | None = start_node_id
    else:
        trigger = next((n for n in nodes.values() if n.get("type") == "trigger"), None)
        if trigger is None:
            return GraphEvaluation(matched=False, error="no trigger node")
        current = trigger["id"]

    actions: list[dict[str, Any]] = []
    trace: list[ConditionTraceEntry] = []
    visited: set[str] = set()
    paused = False
    resume_node_id: str | None = None
    delay_seconds = 0

    try:
        while current is not None and current not in visited:
            visited.add(current)
            node = nodes.get(current)
            if node is None:
                break
            outs = adjacency.get(current, [])
            node_type = node.get("type")

            if node_type == "condition":
                expr = node.get("data", {}).get("expr")
                result = bool(json_logic(expr, context)) if expr is not None else True
                trace.append(ConditionTraceEntry(node_id=current, result=result))
                wanted = "true" if result else "false"
                current = next((t for (t, h) in outs if (h or "true") == wanted), None)
            elif node_type == "switch":
                # Evaluate each case in declared order; take the first whose
                # expression is truthy, else the "default" handle.
                cases = node.get("data", {}).get("cases", []) or []
                chosen: str | None = None
                for case in cases:
                    expr = case.get("expr")
                    matched = bool(json_logic(expr, context)) if expr is not None else True
                    trace.append(ConditionTraceEntry(node_id=current, result=matched))
                    if matched:
                        chosen = case.get("handle")
                        break
                handle = chosen if chosen is not None else "default"
                current = next((t for (t, h) in outs if h == handle), None)
            elif node_type == "delay":
                # Stop here; the dispatcher suspends the run and resumes from the
                # delay's successor once ``delay_seconds`` has elapsed.
                paused = True
                delay_seconds = int(node.get("data", {}).get("delay_seconds", 0) or 0)
                resume_node_id = outs[0][0] if outs else None
                current = None
            elif node_type == "action":
                actions.append(node)
                current = outs[0][0] if outs else None
            else:  # trigger / merge / passthrough
                current = outs[0][0] if outs else None
    except Exception as exc:  # noqa: BLE001 - malformed condition must fail the run, not the batch
        return GraphEvaluation(matched=False, actions=actions, trace=trace, error=str(exc))

    return GraphEvaluation(
        matched=paused or len(actions) > 0,
        actions=actions,
        trace=trace,
        paused=paused,
        resume_node_id=resume_node_id,
        delay_seconds=delay_seconds,
    )
