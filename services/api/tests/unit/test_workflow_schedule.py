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
