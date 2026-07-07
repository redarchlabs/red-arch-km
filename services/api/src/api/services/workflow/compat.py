"""Read-time normalization of legacy (schema_version <= 1) graphs into the BPMN
category vocabulary the token engine executes.

Legacy definitions use five node types (``trigger``/``action``/``condition``/
``switch``/``delay``, plus the vestigial ``merge``/``passthrough``). Published
versions are IMMUTABLE — a DB trigger forbids rewriting a published definition —
so we never persist a normalized graph. :func:`normalize` returns a fresh
in-memory model each time a definition is read for execution.

The mapping is 1:1 and edge handles are already in the shared vocabulary
(``condition`` true/false, ``switch`` ``case-*``/``default``), so **edges pass
through untouched**:

    action       -> task     (task_type send|service; action_type preserved)
    condition    -> gateway   exclusive (expr preserved  -> true/false)
    switch       -> gateway   exclusive (cases preserved -> case-*/default)
    delay        -> event     intermediate timer catch (delay_seconds preserved)
    merge        -> gateway   exclusive (uncontrolled per-token forward)
    passthrough  -> gateway   exclusive (per-token forward)
    trigger      -> trigger   (unchanged)

``merge``/``passthrough`` become *exclusive* gateways deliberately: an exclusive
gateway forwards each arriving token independently with no join wait, which
exactly reproduces the legacy single-path walker's pass-through behavior (the old
evaluator followed ``outs[0][0]``). Mapping them to a parallel/AND join would
deadlock, because the legacy graph's other incoming edges never carry a token.
"""

from __future__ import annotations

from typing import Any

from api.schemas.workflow_definition import WorkflowDefinitionModel, WorkflowNode
from api.services.workflow import constants as C

# Legacy "send"-style actions become BPMN Send Tasks; the rest Service Tasks.
# (Only affects the on-canvas shape / classification — execution still dispatches
# on ``data.action_type`` through the existing action registry.)
_SEND_ACTIONS = frozenset({"send_email", "send_webhook", "send_form"})

_GATEWAY_LEGACY_TYPES = frozenset({"condition", "switch", "merge", "passthrough"})


def _task_type_for_action(action_type: str | None) -> str:
    return C.TASK_SEND if action_type in _SEND_ACTIONS else C.TASK_SERVICE


def _normalize_node(node: WorkflowNode) -> WorkflowNode:
    data = dict(node.data or {})
    if node.type == "action":
        data.setdefault("task_type", _task_type_for_action(data.get("action_type")))
        return node.model_copy(update={"type": C.NODE_TASK, "data": data})
    if node.type in _GATEWAY_LEGACY_TYPES:
        data.setdefault("gateway_type", C.GATEWAY_EXCLUSIVE)
        return node.model_copy(update={"type": C.NODE_GATEWAY, "data": data})
    if node.type == "delay":
        data.setdefault("position", C.EVENT_INTERMEDIATE)
        data.setdefault("event_type", C.EVENT_TIMER)
        data.setdefault("throw_catch", "catch")
        return node.model_copy(update={"type": C.NODE_EVENT, "data": data})
    return node  # trigger or an already-v2 node


def normalize(definition: dict[str, Any] | WorkflowDefinitionModel | None) -> WorkflowDefinitionModel:
    """Return a token-engine-executable model.

    Legacy nodes are mapped to BPMN categories; a graph already free of legacy
    node types is returned parsed-but-unchanged. Never mutates or persists the
    input (safe against the published-version immutability trigger).
    """
    model = (
        definition
        if isinstance(definition, WorkflowDefinitionModel)
        else WorkflowDefinitionModel.parse(definition)
    )
    if not any(node.type in C.LEGACY_NODE_TYPES for node in model.nodes):
        return model
    return model.model_copy(
        update={
            "schema_version": max(model.schema_version, C.SCHEMA_VERSION),
            "nodes": [_normalize_node(node) for node in model.nodes],
        }
    )
