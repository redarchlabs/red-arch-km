"""Scheduled-trigger due-check — interval AND cron.

A workflow's trigger may carry a ``schedule`` block: a fixed interval
(``{every_minutes: N}`` or ``{every_seconds: N}``) or ``{cron: "<expr>"}`` (a
5-field cron expression). This pure function decides whether a workflow is due to
fire, given when it last fired.

``every_seconds`` exists for simulation-style loops — a workflow that advances
shared state (heat rising, a ship moving) reads as broken at one tick per minute.
Note the check is only as fine as the sweep that calls it: the beat interval
(``WORKFLOW_TIMER_INTERVAL``) is the real floor, so an ``every_seconds`` below it
fires once per sweep, not on its stated period.

Cron uses the "catch-up" convention consistent with the interval case: a
never-fired schedule fires on the next sweep, then follows the cron boundaries —
i.e. due when a cron boundary has elapsed since the last run. Kept pure (no DB, no
wall clock of its own) so it's trivially unit-testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def is_schedule_due(schedule: Any, last_run: datetime | None, now: datetime) -> bool:
    """True if a workflow with this ``schedule`` should fire at ``now``.

    ``last_run`` is the timestamp of its most recent scheduled run (None if it has
    never fired). Precedence when more than one is set: cron, then ``every_seconds``,
    then ``every_minutes`` — most specific wins.
    A malformed / empty / invalid schedule is never due (returns False).
    """
    if not isinstance(schedule, dict):
        return False

    cron_expr = schedule.get("cron")
    if isinstance(cron_expr, str) and cron_expr.strip():
        return _cron_due(cron_expr.strip(), last_run, now)

    every_seconds = _as_int(schedule.get("every_seconds"))
    if every_seconds > 0:
        return _interval_due(timedelta(seconds=every_seconds), last_run, now)

    every_minutes = _as_int(schedule.get("every_minutes"))
    if every_minutes <= 0:
        return False
    return _interval_due(timedelta(minutes=every_minutes), last_run, now)


def _interval_due(period: timedelta, last_run: datetime | None, now: datetime) -> bool:
    return last_run is None or (now - last_run) >= period


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _cron_due(cron_expr: str, last_run: datetime | None, now: datetime) -> bool:
    try:
        from croniter import croniter

        if not croniter.is_valid(cron_expr):
            return False
        # The most recent scheduled boundary at or before `now`.
        prev_boundary = croniter(cron_expr, now).get_prev(datetime)
    except Exception:  # noqa: BLE001 - a bad expression is simply "not due"
        return False
    # Fire when a boundary has elapsed since the last run (or it never ran).
    return last_run is None or last_run < prev_boundary
