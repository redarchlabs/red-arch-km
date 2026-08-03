"""Unit tests: a call activity can pass per-invocation inputs to its child run.

A child run used to inherit the parent's ``inputs`` verbatim, so a loop could only ever
call its child with one fixed argument. That makes an otherwise reusable child — "ask
discussion question N" — uncallable from a loop, which is exactly what an autonomous
lesson needs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api.services.workflow.engine import _resolve_call_inputs

pytestmark = pytest.mark.unit


def _node(call_inputs=None):
    data = {} if call_inputs is None else {"call_inputs": call_inputs}
    return SimpleNamespace(data=data)


def _run(vars_=None, inputs=None):
    snap = {"inputs": inputs or {}}
    return SimpleNamespace(input_snapshot=snap, variables=vars_ or {}), snap


def test_without_call_inputs_the_child_inherits_the_parent():
    run, snap = _run(inputs={"week_number": 32})
    assert _resolve_call_inputs(_node(), run, snap) == {"week_number": 32}


def test_call_inputs_are_overlaid_on_the_inherited_ones():
    run, snap = _run(inputs={"week_number": 32})
    out = _resolve_call_inputs(_node({"question_sequence": 3}), run, snap)
    assert out == {"week_number": 32, "question_sequence": 3}


def test_ref_preserves_int_type():
    """A sequence arriving as '3' would never match an integer column filter."""
    run, snap = _run(vars_={"s": {"current_segment": 3}})
    out = _resolve_call_inputs(_node({"question_sequence": {"$ref": "vars.s.current_segment"}}), run, snap)
    assert out["question_sequence"] == 3
    assert isinstance(out["question_sequence"], int)


def test_call_inputs_win_over_an_inherited_key_of_the_same_name():
    run, snap = _run(inputs={"question_sequence": 1})
    out = _resolve_call_inputs(_node({"question_sequence": 7}), run, snap)
    assert out["question_sequence"] == 7


def test_template_values_render():
    run, snap = _run(vars_={"seg": {"title": "Fasting"}})
    out = _resolve_call_inputs(_node({"label": "Segment: {{vars.seg.title}}"}), run, snap)
    assert out["label"] == "Segment: Fasting"


def test_malformed_call_inputs_degrade_to_the_parent_inputs():
    run, snap = _run(inputs={"week_number": 32})
    for bad in ("not a map", [], 7):
        assert _resolve_call_inputs(_node(bad), run, snap) == {"week_number": 32}
