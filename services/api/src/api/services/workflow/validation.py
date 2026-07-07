"""Semantic graph validation for workflow definitions — shared, structured rules.

The Pydantic contract (``schemas/workflow_definition.py``) guarantees a graph is
*structurally* well-formed (known types, unique ids, edges reference real nodes).
This module layers the *semantic* BPMN rules on top and returns a list of
:class:`Issue` keyed by node/edge so the API, the AI assistant, and the frontend
(which mirrors these rules in ``validation.ts``) can surface precise, actionable
feedback. ``error`` issues block publish; ``warning`` issues are advisory.

Checks run against the *normalized* graph (``compat.normalize``) so legacy
condition/switch/delay nodes are validated with the same rules as their BPMN
equivalents.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from api.schemas.workflow_definition import WorkflowDefinitionModel, WorkflowNode
from api.services.workflow import compat
from api.services.workflow import constants as C

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
        }


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def validate_definition(
    definition: dict[str, Any] | WorkflowDefinitionModel | None,
) -> list[Issue]:
    """Return all semantic issues for a workflow graph (empty = clean).

    A structurally-malformed definition returns ``error`` issues describing the
    parse failures rather than raising, so a single call surfaces every problem.
    """
    try:
        raw = (
            definition
            if isinstance(definition, WorkflowDefinitionModel)
            else WorkflowDefinitionModel.parse(definition)
        )
    except ValidationError as exc:
        return [Issue("error", "malformed", _fmt_error(err)) for err in exc.errors()] or [
            Issue("error", "malformed", "definition is not a valid workflow graph")
        ]

    authored_v2 = raw.is_v2
    model = compat.normalize(raw)
    out_edges, incoming = _adjacency(model)

    issues: list[Issue] = []
    _check_triggers(model, issues)
    _check_reachability(model, out_edges, issues)
    _check_gateways(model, out_edges, incoming, issues)
    _check_boundary_events(model, issues)
    _check_event_based_gateways(model, out_edges, issues)
    _check_loops(model, out_edges, issues)
    if authored_v2:
        _check_end_presence(model, issues)
    return issues


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fmt_error(err: dict[str, Any]) -> str:
    loc = ".".join(str(p) for p in err.get("loc", ()))
    msg = err.get("msg", "invalid")
    return f"{loc}: {msg}" if loc else msg


def _adjacency(model: WorkflowDefinitionModel):
    out_edges: dict[str, list] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    for edge in model.edges:
        out_edges[edge.source].append(edge)
        incoming[edge.target] += 1
    return out_edges, incoming


def _boundary_map(model: WorkflowDefinitionModel) -> dict[str, list[str]]:
    """activity id -> boundary event ids attached to it (a non-edge reachability
    link: a boundary event is reachable whenever its host activity is)."""
    by_host: dict[str, list[str]] = defaultdict(list)
    for node in model.nodes:
        if _is_boundary(node):
            host = node.data.get("attached_to")
            if host:
                by_host[host].append(node.id)
    return by_host


def _is_boundary(node: WorkflowNode) -> bool:
    return node.type == C.NODE_EVENT and node.data.get("position") == C.EVENT_BOUNDARY


def _is_catch_target(node: WorkflowNode | None) -> bool:
    if node is None:
        return False
    if node.type == C.NODE_EVENT and node.data.get("position") == C.EVENT_INTERMEDIATE:
        return node.data.get("throw_catch", "catch") == "catch"
    return node.type == C.NODE_TASK and node.task_type == C.TASK_RECEIVE


def _makes_progress(node: WorkflowNode) -> bool:
    """A node that does real work (a task) or waits (a timer/message/signal
    catch) — a loop containing one can't tight-spin."""
    if node.type == C.NODE_TASK:
        return True
    if node.type == C.NODE_EVENT:
        return node.data.get("event_type") in C.WAIT_EVENT_TYPES
    return False


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def _check_triggers(model: WorkflowDefinitionModel, issues: list[Issue]) -> None:
    triggers = [n for n in model.nodes if n.type == C.NODE_TRIGGER]
    if not triggers:
        issues.append(
            Issue("error", "no-trigger", "The workflow has no trigger/start event, so it can never run.")
        )
    elif len(triggers) > 1:
        issues.append(
            Issue(
                "warning",
                "multiple-triggers",
                f"{len(triggers)} start events — a separate run starts from each.",
            )
        )


def _check_reachability(model: WorkflowDefinitionModel, out_edges, issues: list[Issue]) -> None:
    starts = [n.id for n in model.nodes if n.type == C.NODE_TRIGGER]
    boundary_by_host = _boundary_map(model)
    seen: set[str] = set()
    queue = deque(starts)
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        for edge in out_edges.get(current, []):
            queue.append(edge.target)
        for boundary_id in boundary_by_host.get(current, []):
            queue.append(boundary_id)
    for node in model.nodes:
        if node.type == C.NODE_TRIGGER or node.id in seen:
            continue
        issues.append(
            Issue(
                "warning",
                "unreachable",
                f"Node {node.id!r} can't be reached from any start event and won't run.",
                node_id=node.id,
            )
        )


def _check_gateways(model: WorkflowDefinitionModel, out_edges, incoming, issues: list[Issue]) -> None:
    for node in model.nodes:
        if node.type != C.NODE_GATEWAY:
            continue
        gateway_type = node.gateway_type or C.GATEWAY_EXCLUSIVE
        out_count = len(out_edges.get(node.id, []))
        in_count = incoming.get(node.id, 0)
        if gateway_type in C.FORKING_GATEWAY_TYPES:
            if out_count < 2 and in_count < 2:
                issues.append(
                    Issue(
                        "warning",
                        "degenerate-gateway",
                        f"{gateway_type} gateway {node.id!r} neither forks (needs ≥2 outgoing) "
                        f"nor joins (needs ≥2 incoming).",
                        node_id=node.id,
                    )
                )
        elif gateway_type == C.GATEWAY_EXCLUSIVE and out_count >= 2:
            handles = [edge.source_handle for edge in out_edges.get(node.id, [])]
            branches_on_condition = node.data.get("expr") is not None or bool(node.data.get("cases"))
            if branches_on_condition and C.HANDLE_DEFAULT not in handles:
                issues.append(
                    Issue(
                        "warning",
                        "exclusive-no-default",
                        f"Exclusive gateway {node.id!r} has no default branch; a token matching no "
                        f"condition would stop here.",
                        node_id=node.id,
                    )
                )


def _check_boundary_events(model: WorkflowDefinitionModel, issues: list[Issue]) -> None:
    ids = {n.id: n for n in model.nodes}
    for node in model.nodes:
        if not _is_boundary(node):
            continue
        host_id = node.data.get("attached_to")
        if not host_id:
            issues.append(
                Issue(
                    "error",
                    "boundary-unattached",
                    f"Boundary event {node.id!r} is not attached to an activity.",
                    node_id=node.id,
                )
            )
            continue
        host = ids.get(host_id)
        if host is None:
            issues.append(
                Issue(
                    "error",
                    "boundary-bad-attach",
                    f"Boundary event {node.id!r} is attached to unknown node {host_id!r}.",
                    node_id=node.id,
                )
            )
        elif host.type != C.NODE_TASK:
            issues.append(
                Issue(
                    "warning",
                    "boundary-nonactivity",
                    f"Boundary event {node.id!r} should attach to a task, not a {host.type!r}.",
                    node_id=node.id,
                )
            )


def _check_event_based_gateways(model: WorkflowDefinitionModel, out_edges, issues: list[Issue]) -> None:
    ids = {n.id: n for n in model.nodes}
    for node in model.nodes:
        if node.type != C.NODE_GATEWAY or node.gateway_type != C.GATEWAY_EVENT_BASED:
            continue
        for edge in out_edges.get(node.id, []):
            if not _is_catch_target(ids.get(edge.target)):
                issues.append(
                    Issue(
                        "error",
                        "event-gateway-target",
                        f"Event-based gateway {node.id!r} must lead only to catch events or receive "
                        f"tasks; {edge.target!r} is not one.",
                        node_id=node.id,
                        edge_id=edge.id,
                    )
                )


def _check_loops(model: WorkflowDefinitionModel, out_edges, issues: list[Issue]) -> None:
    """Cycles are allowed (the token engine bounds them with a step budget), but a
    loop with no task/wait node can spin without progress — warn on those."""
    for scc in _strongly_connected(model, out_edges):
        if len(scc) == 1:
            node_id = next(iter(scc))
            self_loop = any(e.target == node_id for e in out_edges.get(node_id, []))
            if not self_loop:
                continue
        ids = {n.id: n for n in model.nodes}
        if not any(_makes_progress(ids[nid]) for nid in scc if nid in ids):
            issues.append(
                Issue(
                    "warning",
                    "loop-no-progress",
                    f"Loop through {sorted(scc)} contains no task or wait step and may spin without "
                    f"making progress.",
                    node_id=sorted(scc)[0],
                )
            )


def _check_end_presence(model: WorkflowDefinitionModel, issues: list[Issue]) -> None:
    has_end = any(
        n.type == C.NODE_EVENT and n.data.get("position") == C.EVENT_END for n in model.nodes
    )
    if not has_end:
        issues.append(
            Issue("warning", "no-end-event", "No explicit end event; add one so the flow's completion reads clearly.")
        )


def _strongly_connected(model: WorkflowDefinitionModel, out_edges) -> list[set[str]]:
    """Tarjan's SCC over the node graph (iterative — safe for any graph size)."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[set[str]] = []
    counter = 0
    node_ids = [n.id for n in model.nodes]

    for root in node_ids:
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_idx = work[-1]
            if child_idx == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            targets = out_edges.get(node, [])
            if child_idx < len(targets):
                work[-1] = (node, child_idx + 1)
                nxt = targets[child_idx].target
                if nxt not in index_of:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
            else:
                if low[node] == index_of[node]:
                    component: set[str] = set()
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.add(w)
                        if w == node:
                            break
                    sccs.append(component)
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
    return sccs
