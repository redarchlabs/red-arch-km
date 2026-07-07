"""Unit tests for the public-endpoint sliding-window rate limiter."""

from __future__ import annotations

from api.services.rate_limit import SlidingWindowLimiter


def test_allows_up_to_max_then_blocks():
    lim = SlidingWindowLimiter(3)
    assert lim.allow("k", now=100.0)
    assert lim.allow("k", now=100.1)
    assert lim.allow("k", now=100.2)
    assert not lim.allow("k", now=100.3)  # 4th within window blocked


def test_window_slides():
    lim = SlidingWindowLimiter(2)
    assert lim.allow("k", now=0.0)
    assert lim.allow("k", now=1.0)
    assert not lim.allow("k", now=2.0)  # still within 60s
    assert lim.allow("k", now=61.0)  # oldest hit aged out of the window


def test_keys_are_independent():
    lim = SlidingWindowLimiter(1)
    assert lim.allow("a", now=0.0)
    assert lim.allow("b", now=0.0)
    assert not lim.allow("a", now=0.1)


def test_max_clamped_to_at_least_one():
    lim = SlidingWindowLimiter(0)
    assert lim.allow("k", now=0.0)
    assert not lim.allow("k", now=0.1)
