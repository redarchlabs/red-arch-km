"""Unit tests for the error-boundary matcher (_error_boundary_for)."""

from __future__ import annotations

import pytest
from api.schemas.workflow_definition import WorkflowDefinitionModel
from api.services.workflow.engine import _error_boundary_for

pytestmark = pytest.mark.unit


def _model(*boundaries: dict) -> WorkflowDefinitionModel:
    nodes = [{"id": "task", "type": "task", "data": {"task_type": "service", "action_type": "log"}}]
    nodes.extend(boundaries)
    return WorkflowDefinitionModel.parse({"schema_version": 2, "nodes": nodes, "edges": []})


def _boundary(node_id: str, *, host: str = "task", error_code: str | None = None) -> dict:
    data = {"position": "boundary", "event_type": "error", "attached_to": host}
    if error_code is not None:
        data["error_code"] = error_code
    return {"id": node_id, "type": "event", "data": data}


def test_none_when_no_boundary() -> None:
    assert _error_boundary_for(_model(), "task") is None


def test_catch_all_matches_any_error() -> None:
    m = _model(_boundary("b"))
    assert _error_boundary_for(m, "task").id == "b"
    assert _error_boundary_for(m, "task", "ANY_CODE").id == "b"


def test_boundary_only_matches_its_host() -> None:
    m = _model(_boundary("b", host="other"))
    assert _error_boundary_for(m, "task") is None


def test_code_specific_preferred_over_catch_all() -> None:
    m = _model(_boundary("b_all"), _boundary("b_boom", error_code="BOOM"))
    assert _error_boundary_for(m, "task", "BOOM").id == "b_boom"


def test_catch_all_is_fallback_when_code_mismatches() -> None:
    m = _model(_boundary("b_all"), _boundary("b_boom", error_code="BOOM"))
    # A different code falls back to the catch-all rather than the code-specific one.
    assert _error_boundary_for(m, "task", "OTHER").id == "b_all"


def test_no_match_when_only_code_specific_and_code_differs() -> None:
    m = _model(_boundary("b_boom", error_code="BOOM"))
    # No catch-all, and the code doesn't match → nothing catches it.
    assert _error_boundary_for(m, "task", "OTHER") is None
    # A failure with no surfaced code also can't match a code-specific boundary.
    assert _error_boundary_for(m, "task", None) is None
