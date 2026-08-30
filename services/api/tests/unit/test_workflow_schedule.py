"""Unit tests for the scheduled-trigger due-check (interval + cron)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api.services.workflow.schedule import is_schedule_due

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


# --- interval ------------------------------------------------------------- #
def test_interval_never_fired_is_due() -> None:
    assert is_schedule_due({"every_minutes": 60}, None, _NOW) is True


def test_interval_not_due_before_period() -> None:
    assert is_schedule_due({"every_minutes": 60}, datetime(2026, 1, 1, 9, 30, tzinfo=UTC), _NOW) is False


def test_interval_due_after_period() -> None:
    assert is_schedule_due({"every_minutes": 60}, datetime(2026, 1, 1, 8, 0, tzinfo=UTC), _NOW) is True


def test_interval_zero_or_missing_never_due() -> None:
    assert is_schedule_due({"every_minutes": 0}, None, _NOW) is False
    assert is_schedule_due({}, None, _NOW) is False


# --- cron ----------------------------------------------------------------- #
def test_cron_never_fired_is_due() -> None:
    assert is_schedule_due({"cron": "0 9 * * *"}, None, _NOW) is True


def test_cron_due_when_boundary_elapsed_since_last() -> None:
    # last=08:00, boundary at 09:00 has passed by now=10:00 → due.
    assert is_schedule_due({"cron": "0 9 * * *"}, datetime(2026, 1, 1, 8, 0, tzinfo=UTC), _NOW) is True


def test_cron_not_due_when_no_boundary_since_last() -> None:
    # last=09:30 is after today's 09:00 boundary; next is tomorrow → not due at 10:00.
    assert is_schedule_due({"cron": "0 9 * * *"}, datetime(2026, 1, 1, 9, 30, tzinfo=UTC), _NOW) is False


def test_cron_due_across_days() -> None:
    assert is_schedule_due({"cron": "0 9 * * *"}, datetime(2025, 12, 31, 9, 0, tzinfo=UTC), _NOW) is True


def test_cron_takes_precedence_over_interval() -> None:
    # cron says not due; interval would say due — cron wins.
    schedule = {"cron": "0 9 * * *", "every_minutes": 1}
    assert is_schedule_due(schedule, datetime(2026, 1, 1, 9, 30, tzinfo=UTC), _NOW) is False


def test_invalid_cron_is_never_due() -> None:
    assert is_schedule_due({"cron": "not a cron"}, None, _NOW) is False
    assert is_schedule_due({"cron": ""}, None, _NOW) is False


def test_non_dict_schedule_is_never_due() -> None:
    assert is_schedule_due(None, None, _NOW) is False
    assert is_schedule_due("0 9 * * *", None, _NOW) is False


# --- sub-minute interval --------------------------------------------------- #
# `every_minutes` floors a self-driven workflow at one tick per minute, which is
# too coarse for a simulation loop (a reactor warming, a ship moving). These cover
# `every_seconds`, which takes precedence over `every_minutes` when both are set.
def test_seconds_never_fired_is_due() -> None:
    assert is_schedule_due({"every_seconds": 5}, None, _NOW) is True


def test_seconds_not_due_before_period() -> None:
    last = datetime(2026, 1, 1, 9, 59, 58, tzinfo=UTC)  # 2s ago
    assert is_schedule_due({"every_seconds": 5}, last, _NOW) is False


def test_seconds_due_after_period() -> None:
    last = datetime(2026, 1, 1, 9, 59, 50, tzinfo=UTC)  # 10s ago
    assert is_schedule_due({"every_seconds": 5}, last, _NOW) is True


def test_seconds_zero_or_negative_never_due() -> None:
    assert is_schedule_due({"every_seconds": 0}, None, _NOW) is False
    assert is_schedule_due({"every_seconds": -5}, None, _NOW) is False


def test_seconds_takes_precedence_over_minutes() -> None:
    # minutes says not due (30s since a 60m interval); seconds says due.
    last = datetime(2026, 1, 1, 9, 59, 30, tzinfo=UTC)
    assert is_schedule_due({"every_minutes": 60, "every_seconds": 5}, last, _NOW) is True


def test_cron_still_takes_precedence_over_seconds() -> None:
    schedule = {"cron": "0 9 * * *", "every_seconds": 1}
    assert is_schedule_due(schedule, datetime(2026, 1, 1, 9, 30, tzinfo=UTC), _NOW) is False
