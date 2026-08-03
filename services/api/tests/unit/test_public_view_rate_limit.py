"""Unit tests: a shared view's rate ceiling has to be sized for an AUDIENCE.

The limiter on ``/api/public/views/{token}`` is per TOKEN, not per device — one
counter shared by every phone on the link. It used to be ``max(rate_limit_per_minute,
120)``. A quiz page re-rendering every 2s costs 30 requests/minute per phone, so 120
was exhausted by the FIFTH phone to scan the QR code, and the whole room saw "failed
to load" at once.

These pin the arithmetic that makes that a bug, so the ceiling can't quietly drift
back below a classroom.
"""

from __future__ import annotations

import pytest
from api.config import Settings

pytestmark = pytest.mark.unit


def _settings(**over) -> Settings:
    base = {"secret_key": "x", "database_url": "postgresql+asyncpg://u:p@h/d"}
    return Settings(**{**base, **over})


def test_default_ceiling_covers_a_class_sized_room() -> None:
    """30 phones x 30 polls/min = 900. The default must clear that with headroom."""
    ceiling = _settings().public_view_rate_limit_per_minute
    polls_per_phone_per_minute = 60_000 / 2000  # refresh_ms = 2000 on the quiz page
    assert ceiling >= 30 * polls_per_phone_per_minute


def test_default_is_far_above_the_old_flat_120() -> None:
    # 120 was the regression: four phones and the fifth got a 429.
    assert _settings().public_view_rate_limit_per_minute > 120


def test_ceiling_is_independent_of_the_generic_api_limit() -> None:
    """It used to be max(rate_limit_per_minute, 120), so tightening the ordinary API
    limit could not raise it and raising it dragged every other endpoint along."""
    tight = _settings(rate_limit_per_minute=10)
    assert tight.public_view_rate_limit_per_minute >= 900


def test_ceiling_is_overridable_for_a_bigger_room() -> None:
    assert _settings(public_view_rate_limit_per_minute=5000).public_view_rate_limit_per_minute == 5000


def test_supported_device_count_is_derivable() -> None:
    """The number a teacher actually cares about: how many phones can this hold?"""
    ceiling = _settings().public_view_rate_limit_per_minute
    supported = ceiling / (60_000 / 2000)
    assert supported >= 30, f"only {supported:.0f} phones fit under the ceiling"
