"""Gateways/scripts see the same clock tokens actions already have.

``_trigger_context`` (actions) has exposed ``now``/``today`` since the template
polish pass, but ``_expr_context`` (gateways, decision tables, script
transforms) did not — so a timer-resumed gateway could not ask "is this record's
due date in the past?", which is exactly the question an overdue check needs.
"""

from __future__ import annotations

import datetime as dt

from api.services.workflow.engine import _expr_context
from api.services.workflow.jsonlogic import json_logic


class _Run:
    """Stand-in for WorkflowRun: _expr_context reads only these attributes."""

    def __init__(self, snapshot=None, variables=None):
        self.input_snapshot = snapshot or {}
        self.variables = variables or {}


def test_context_carries_now_and_today() -> None:
    ctx = _expr_context(_Run())
    today = dt.datetime.now(dt.UTC).date().isoformat()
    assert ctx["today"] == today
    # ISO-8601 timestamp that starts with today's date.
    assert ctx["now"].startswith(today)


def test_gateway_can_compare_a_due_date_against_today() -> None:
    ctx = _expr_context(_Run(variables={"cur": {"due_date": "2000-01-01", "status": "in_progress"}}))
    overdue = {
        "and": [
            {"!=": [{"var": "vars.cur.status"}, "completed"]},
            {"!!": {"var": "vars.cur.due_date"}},
            {"<": [{"var": "vars.cur.due_date"}, {"var": "today"}]},
        ]
    }
    assert json_logic(overdue, ctx) is True
    # A future due date is NOT overdue.
    ctx["vars"]["cur"]["due_date"] = "2999-12-31"
    assert json_logic(overdue, ctx) is False
    # No due date at all is NOT overdue.
    ctx["vars"]["cur"]["due_date"] = ""
    assert json_logic(overdue, ctx) is False


def test_existing_keys_unchanged() -> None:
    ctx = _expr_context(_Run(snapshot={"after": {"id": "x"}, "inputs": {"a": 1}}, variables={"v": 2}))
    assert ctx["after"] == {"id": "x"}
    assert ctx["inputs"] == {"a": 1}
    assert ctx["vars"] == {"v": 2}
    assert ctx["before"] is None
