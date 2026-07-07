"""Pydantic contract for the workflow graph ``definition`` (schema_version 2).

Replaces the opaque ``dict[str, Any]`` the API accepted for a version's graph.
This is the **structural** contract — well-formed unique ids, known node types and
subtypes, edges that reference real nodes, in-vocabulary reserved handles. The
**semantic** graph rules (reachability, gateway arity, boundary attachment, loop
warnings) live in ``services/workflow/validation.py`` and are shared with the
frontend: keeping them separate lets the API reject malformed JSON with a precise
422 while surfacing graph-quality issues as structured warnings/errors an author
(or the AI assistant) can act on.

Lenient by design where it must be — a node's ``data`` stays a free-form dict so
legacy (schema_version <= 1) definitions and forward-compatible fields parse
without loss. Only the discriminators the engine routes on are validated here.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.services.workflow import constants as C

# Matches the frontend's ``newNodeId`` output (``<prefix>_<base36>``) and legacy
# ids like ``"trigger"``. Bounded so an id can't bloat the JSONB or a log line.
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Position(BaseModel):
    """Canvas coordinates. Lenient: any extra React Flow layout keys are dropped."""

    model_config = ConfigDict(extra="ignore")

    x: float = 0.0
    y: float = 0.0


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    source: str
    target: str
    # Which branch handle this edge leaves the source by (true/false/default/
    # error/boundary/case-*). ``None`` = the node's sole/default out-edge.
    source_handle: str | None = None
    target_handle: str | None = None

    @property
    def label(self) -> str:
        return self.id or f"{self.source}->{self.target}"


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    position: Position = Field(default_factory=Position)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_well_formed(cls, v: str) -> str:
        if not _NODE_ID_RE.match(v):
            raise ValueError(f"node id {v!r} must match [A-Za-z0-9_-]{{1,64}}")
        return v

    @field_validator("type")
    @classmethod
    def _type_known(cls, v: str) -> str:
        if v not in C.NODE_TYPES:
            raise ValueError(f"unknown node type {v!r}")
        return v

    @model_validator(mode="after")
    def _subtype_in_vocab(self) -> WorkflowNode:
        """Validate the engine-routing discriminator, when present. A missing
        discriminator is allowed (defaults are applied downstream / by the UI);
        an *unknown* one is a hard error so a typo can't silently misroute."""
        data = self.data or {}
        if self.type == C.NODE_TASK:
            task_type = data.get("task_type")
            if task_type is not None and task_type not in C.TASK_TYPES:
                raise ValueError(f"task node {self.id!r}: unknown task_type {task_type!r}")
        elif self.type == C.NODE_GATEWAY:
            gateway_type = data.get("gateway_type")
            if gateway_type is not None and gateway_type not in C.GATEWAY_TYPES:
                raise ValueError(f"gateway node {self.id!r}: unknown gateway_type {gateway_type!r}")
        elif self.type == C.NODE_EVENT:
            position = data.get("position")
            if position is not None and position not in C.EVENT_POSITIONS:
                raise ValueError(f"event node {self.id!r}: unknown position {position!r}")
            event_type = data.get("event_type")
            if event_type is not None and event_type not in C.EVENT_TYPES:
                raise ValueError(f"event node {self.id!r}: unknown event_type {event_type!r}")
        return self

    # ---- convenience accessors (engine + tools) ------------------------- #
    @property
    def task_type(self) -> str | None:
        return self.data.get("task_type") if self.type == C.NODE_TASK else None

    @property
    def gateway_type(self) -> str | None:
        return self.data.get("gateway_type") if self.type == C.NODE_GATEWAY else None

    @property
    def is_wait_state(self) -> bool:
        """Does advancing to this node PARK a token (vs. run a synchronous job)?"""
        if self.type == C.NODE_TASK:
            return self.task_type in C.WAIT_TASK_TYPES
        if self.type == C.NODE_EVENT:
            pos = self.data.get("position")
            if pos == C.EVENT_INTERMEDIATE:
                return self.data.get("throw_catch", "catch") == "catch" and (
                    self.data.get("event_type") in C.WAIT_EVENT_TYPES
                )
            if pos == C.EVENT_BOUNDARY:
                return True
        return False


class WorkflowDefinitionModel(BaseModel):
    """A parsed, structurally-valid workflow graph.

    Use :meth:`parse` at the API boundary (save_draft / publish) to turn the
    stored/submitted JSON into this model; a ``ValueError`` (raised as pydantic
    ``ValidationError``) means the JSON is malformed. Semantic quality checks are
    a separate pass (``validation.validate_definition``).
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _structural(self) -> WorkflowDefinitionModel:
        seen: set[str] = set()
        dupes: set[str] = set()
        for node in self.nodes:
            if node.id in seen:
                dupes.add(node.id)
            seen.add(node.id)
        if dupes:
            raise ValueError(f"duplicate node ids: {sorted(dupes)}")
        for edge in self.edges:
            if edge.source not in seen:
                raise ValueError(f"edge {edge.label!r} references unknown source {edge.source!r}")
            if edge.target not in seen:
                raise ValueError(f"edge {edge.label!r} references unknown target {edge.target!r}")
        return self

    @classmethod
    def parse(cls, definition: dict[str, Any] | None) -> WorkflowDefinitionModel:
        return cls.model_validate(definition or {})

    @property
    def is_v2(self) -> bool:
        """True when this graph should run on the token engine.

        ``schema_version >= 2`` OR any node using the new BPMN vocabulary — the
        latter guards against a definition that adopts new nodes without bumping
        the version. Legacy v1 graphs (only ``action``/``condition``/``switch``/
        ``delay``/``merge``/``passthrough``) stay on the legacy walker.
        """
        if self.schema_version >= C.SCHEMA_VERSION:
            return True
        return any(n.type in C.NEW_NODE_TYPES and n.type != C.NODE_TRIGGER for n in self.nodes)

    def node_by_id(self, node_id: str) -> WorkflowNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)
