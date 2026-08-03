"""Unit tests: a timer's wait may come from run data, not just a literal in the graph.

``delay_seconds`` used to be read as a bare ``int`` off the node, so every wait was
baked into the workflow definition. A robot-paced lesson needs per-question and
per-segment waits that live in records, so the value is now resolved through the same
``$ref`` / ``{{ }}`` machinery as action config first.

The fallback behaviour is the point of most of these: a malformed delay must advance
the token, never fail the run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api.services.workflow.engine import _resolve_delay_seconds

pytestmark = pytest.mark.unit


def _run(*, vars_: dict | None = None, inputs: dict | None = None):
    """A stand-in for WorkflowRun: _expr_context only reads these two attributes."""
    return SimpleNamespace(input_snapshot={"inputs": inputs or {}}, variables=vars_ or {})


# --- literals keep working exactly as before -------------------------------------- #


def test_literal_int_is_unchanged():
    assert _resolve_delay_seconds({"delay_seconds": 3600}, _run()) == 3600


def test_missing_delay_is_zero():
    assert _resolve_delay_seconds({}, _run()) == 0


def test_numeric_string_literal_is_coerced():
    assert _resolve_delay_seconds({"delay_seconds": "20"}, _run()) == 20


def test_float_rounds_to_whole_seconds():
    """Rounds rather than truncates: truncation would turn a sub-second wait into 0,
    which advances instantly — the exact failure this timer exists to prevent."""
    assert _resolve_delay_seconds({"delay_seconds": 2.9}, _run()) == 3
    assert _resolve_delay_seconds({"delay_seconds": 2.1}, _run()) == 2
    assert _resolve_delay_seconds({"delay_seconds": 0.6}, _run()) == 1


def test_negative_delay_clamps_to_zero():
    """A negative resume_at would schedule in the past and resume instantly anyway."""
    assert _resolve_delay_seconds({"delay_seconds": -5}, _run()) == 0


# --- the new capability: the wait comes from the run ------------------------------ #


def test_ref_envelope_reads_a_captured_variable():
    """The quiz case: each question carries its own seconds_allowed."""
    run = _run(vars_={"q": {"seconds_allowed": 20}})
    node = {"delay_seconds": {"$ref": "vars.q.seconds_allowed"}}
    assert _resolve_delay_seconds(node, run) == 20


def test_template_string_reads_a_manual_input():
    run = _run(inputs={"wait": 45})
    assert _resolve_delay_seconds({"delay_seconds": "{{ inputs.wait }}"}, run) == 45


def test_ref_to_a_string_field_still_coerces():
    """Record fields often come back as text; a '30' must not become 0."""
    run = _run(vars_={"seg": {"pause": "30"}})
    assert _resolve_delay_seconds({"delay_seconds": {"$ref": "vars.seg.pause"}}, run) == 30


# --- malformed input must degrade, never raise ------------------------------------ #


def test_unresolvable_ref_falls_back_to_zero():
    assert _resolve_delay_seconds({"delay_seconds": {"$ref": "vars.nope.missing"}}, _run()) == 0


def test_non_numeric_value_falls_back_to_zero():
    assert _resolve_delay_seconds({"delay_seconds": "not a number"}, _run()) == 0


def test_none_run_leaves_templates_unresolved_without_raising():
    """Defensive: _dispatch_event's run defaults to None for older call sites."""
    assert _resolve_delay_seconds({"delay_seconds": "{{ inputs.wait }}"}, None) == 0
    assert _resolve_delay_seconds({"delay_seconds": 15}, None) == 15


# --- the two engines must fail at the same budget --------------------------------- #


def test_run_step_budget_is_shared_between_engines():
    """dispatcher.py used to declare its own copy of 200; two copies silently diverge."""
    from api.services.workflow.dispatcher import MAX_RUN_STEPS as dispatcher_budget
    from api.services.workflow.engine import MAX_RUN_STEPS as engine_budget

    assert dispatcher_budget == engine_budget
    assert engine_budget > 0


# --- delay_ms + fallback chain ----------------------------------------------------- #
# The robot's /perform reports the performance's real duration_ms. A driving workflow
# waits on that, but must NOT charge ahead mid-sentence when the field is absent (a robot
# on older firmware), so delay_seconds stays as the fallback.


def test_delay_ms_converts_to_seconds():
    assert _resolve_delay_seconds({"delay_ms": 45000}, _run()) == 45


def test_delay_ms_wins_over_delay_seconds():
    node = {"delay_ms": 30000, "delay_seconds": 99}
    assert _resolve_delay_seconds(node, _run()) == 30


def test_missing_delay_ms_falls_back_to_delay_seconds():
    """The compatibility case: robot did not report duration_ms, use the computed hold."""
    run = _run(vars_={"perform": {"body": {}}, "seg": {"hold_seconds": 57}})
    node = {
        "delay_ms": {"$ref": "vars.perform.body.duration_ms"},
        "delay_seconds": {"$ref": "vars.seg.hold_seconds"},
    }
    assert _resolve_delay_seconds(node, run) == 57


def test_reported_duration_is_used_when_present():
    run = _run(vars_={"perform": {"body": {"duration_ms": 41200}}, "seg": {"hold_seconds": 57}})
    node = {
        "delay_ms": {"$ref": "vars.perform.body.duration_ms"},
        "delay_seconds": {"$ref": "vars.seg.hold_seconds"},
    }
    assert _resolve_delay_seconds(node, run) == 41


def test_zero_or_null_delay_ms_does_not_shadow_the_fallback():
    for bad in (0, None, "", "junk"):
        run = _run(vars_={"seg": {"hold_seconds": 57}})
        node = {"delay_ms": bad, "delay_seconds": {"$ref": "vars.seg.hold_seconds"}}
        assert _resolve_delay_seconds(node, run) == 57, f"delay_ms={bad!r} shadowed the fallback"


def test_sub_second_duration_rounds_rather_than_truncating_to_zero():
    assert _resolve_delay_seconds({"delay_ms": 800}, _run()) == 1
