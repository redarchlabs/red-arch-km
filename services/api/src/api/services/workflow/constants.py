"""Shared vocabulary for the workflow graph definition (schema_version 2).

The token engine, the authoring/validation layer, and the AI-assistant tools all
key off these constants, and the frontend mirrors them
(``ui/src/components/workflows/nodes/nodeMeta.ts``). A contract test keeps the two
in sync.

Design: a node's ``type`` is a BPMN *category* that drives both the on-canvas
shape (frontend) and the job-vs-wait token semantics (backend); the concrete
subtype lives in ``data`` (``task_type`` / ``gateway_type`` / event ``position`` +
``event_type``). This is the Camunda split — it keeps the React Flow ``nodeTypes``
map small while giving the engine the one bit it needs from the node alone.
"""

from __future__ import annotations

# The definition schema version introduced with the BPMN-capable token engine.
# Absent or 1 => legacy definition (interpreted via ``compat.py``, run by the
# legacy walker); >= 2 => BPMN node vocabulary, run by the token engine.
SCHEMA_VERSION = 2

# --- Node categories -------------------------------------------------------- #
NODE_TRIGGER = "trigger"
NODE_TASK = "task"
NODE_GATEWAY = "gateway"
NODE_EVENT = "event"

# Legacy node types (schema_version <= 1). Retained and INTERPRETED, never
# rewritten — published versions are immutable, so ``compat.normalize`` maps
# these to categories at read time only.
LEGACY_NODE_TYPES = frozenset({"action", "condition", "switch", "delay", "merge", "passthrough"})

NEW_NODE_TYPES = frozenset({NODE_TRIGGER, NODE_TASK, NODE_GATEWAY, NODE_EVENT})
NODE_TYPES = NEW_NODE_TYPES | LEGACY_NODE_TYPES

# --- task_type (``data.task_type`` on a ``task`` node) ---------------------- #
TASK_SERVICE = "service"
TASK_SEND = "send"
TASK_SCRIPT = "script"
TASK_BUSINESS_RULE = "businessRule"
TASK_USER = "user"
TASK_RECEIVE = "receive"
TASK_CALL = "call"
TASK_SUBPROCESS = "subProcess"
TASK_MANUAL = "manual"
TASK_TYPES = frozenset(
    {
        TASK_SERVICE,
        TASK_SEND,
        TASK_SCRIPT,
        TASK_BUSINESS_RULE,
        TASK_USER,
        TASK_RECEIVE,
        TASK_CALL,
        TASK_SUBPROCESS,
        TASK_MANUAL,
    }
)
# Wait-state task types park a token until an external signal (timer / user
# completion / correlated message / child-run finish); the rest are synchronous
# jobs. The engine needs this classification from the node type + task_type.
WAIT_TASK_TYPES = frozenset({TASK_USER, TASK_RECEIVE, TASK_CALL, TASK_SUBPROCESS, TASK_MANUAL})

# --- gateway_type (``data.gateway_type`` on a ``gateway`` node) ------------- #
GATEWAY_EXCLUSIVE = "exclusive"
GATEWAY_PARALLEL = "parallel"
GATEWAY_INCLUSIVE = "inclusive"
GATEWAY_EVENT_BASED = "event_based"
GATEWAY_TYPES = frozenset({GATEWAY_EXCLUSIVE, GATEWAY_PARALLEL, GATEWAY_INCLUSIVE, GATEWAY_EVENT_BASED})
# Gateways that FORK (>=2 outgoing) vs converge (JOIN). A node is a join when it
# has >=2 incoming edges; the same gateway_type describes both split and join.
FORKING_GATEWAY_TYPES = frozenset({GATEWAY_PARALLEL, GATEWAY_INCLUSIVE})

# --- event position + type (``data.position`` / ``data.event_type``) -------- #
EVENT_INTERMEDIATE = "intermediate"
EVENT_END = "end"
EVENT_BOUNDARY = "boundary"
EVENT_POSITIONS = frozenset({EVENT_INTERMEDIATE, EVENT_END, EVENT_BOUNDARY})

EVENT_TIMER = "timer"
EVENT_MESSAGE = "message"
EVENT_SIGNAL = "signal"
EVENT_ERROR = "error"
EVENT_ESCALATION = "escalation"
EVENT_TERMINATE = "terminate"
EVENT_NONE = "none"
EVENT_TYPES = frozenset(
    {EVENT_TIMER, EVENT_MESSAGE, EVENT_SIGNAL, EVENT_ERROR, EVENT_ESCALATION, EVENT_TERMINATE, EVENT_NONE}
)
# Event types that a boundary/intermediate CATCH parks a token on until fired.
WAIT_EVENT_TYPES = frozenset({EVENT_TIMER, EVENT_MESSAGE, EVENT_SIGNAL})

# --- Reserved edge ``source_handle`` values (branch routing) ---------------- #
# Node-defined branch handles (e.g. ``case-<id>``) may exist beyond these.
HANDLE_TRUE = "true"
HANDLE_FALSE = "false"
HANDLE_DEFAULT = "default"
HANDLE_ERROR = "error"
HANDLE_BOUNDARY = "boundary"
RESERVED_HANDLES = frozenset({HANDLE_TRUE, HANDLE_FALSE, HANDLE_DEFAULT, HANDLE_ERROR, HANDLE_BOUNDARY})
