"""Unit tests for the workflow retry policy + full-jitter backoff (pure functions)."""

from __future__ import annotations

import pytest
from api.services.workflow.retry import (
    RetryPolicy,
    attempts_so_far,
    backoff,
    clear_attempts,
    read_policy,
    record_attempt,
)

pytestmark = pytest.mark.unit


# --- read_policy ---------------------------------------------------------- #
def test_no_policy_means_single_attempt() -> None:
    assert read_policy(None).max_attempts == 1
    assert read_policy({}).max_attempts == 1
    assert read_policy({"retry": {}}).max_attempts == 1  # empty spec = no retry
    assert not read_policy(None).retries_enabled


def test_policy_defaults_and_parsing() -> None:
    p = read_policy({"retry": {"max_attempts": 4}})
    assert p.max_attempts == 4
    assert p.base_delay == 1.0
    assert p.max_delay == 300.0
    assert p.retries_enabled


def test_policy_clamps_out_of_range_values() -> None:
    assert read_policy({"retry": {"max_attempts": 9999}}).max_attempts == 20  # cap
    assert read_policy({"retry": {"max_attempts": 0}}).max_attempts == 1  # floor
    # base_delay is respected; max_delay is floored to at least base_delay.
    p = read_policy({"retry": {"max_attempts": 3, "base_delay_seconds": 50, "max_delay_seconds": 10}})
    assert p.base_delay == 50.0
    assert p.max_delay == 50.0


def test_policy_rejects_bad_types() -> None:
    # bool is an int subclass — must NOT be read as a numeric policy value.
    assert read_policy({"retry": True}).max_attempts == 1
    assert read_policy({"retry": {"max_attempts": True}}).max_attempts == 3  # falls back to default
    assert read_policy({"retry": {"max_attempts": "lots"}}).max_attempts == 3


# --- backoff (full jitter) ------------------------------------------------ #
def test_backoff_is_within_full_jitter_bounds() -> None:
    p = RetryPolicy(max_attempts=6, base_delay=2.0, max_delay=1000.0)
    for attempt in range(6):
        ceiling = min(1000.0, 2.0 * (2 ** attempt))
        for _ in range(50):
            delay = backoff(attempt, p)
            assert 0.0 <= delay <= ceiling


def test_backoff_respects_cap() -> None:
    p = RetryPolicy(max_attempts=20, base_delay=1.0, max_delay=5.0)
    for _ in range(100):
        assert backoff(15, p) <= 5.0  # 2**15 would be huge; cap holds


def test_backoff_zero_base_is_immediate() -> None:
    p = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=100.0)
    assert backoff(0, p) == 0.0
    assert backoff(5, p) == 0.0


# --- attempt counters ----------------------------------------------------- #
def test_attempt_counter_roundtrip() -> None:
    assert attempts_so_far(None, "n1") == 0
    data = record_attempt(None, "n1", 1)
    assert attempts_so_far(data, "n1") == 1
    data = record_attempt(data, "n1", 2)
    assert attempts_so_far(data, "n1") == 2
    # independent per node
    data = record_attempt(data, "n2", 1)
    assert attempts_so_far(data, "n1") == 2
    assert attempts_so_far(data, "n2") == 1


def test_record_attempt_does_not_mutate_input() -> None:
    original = {"other": "keep"}
    updated = record_attempt(original, "n1", 1)
    assert "other" in updated  # preserves unrelated keys
    assert "_retries" not in original  # input untouched


def test_clear_attempts_removes_only_that_node() -> None:
    data = record_attempt(record_attempt(None, "n1", 2), "n2", 1)
    cleared = clear_attempts(data, "n1")
    assert attempts_so_far(cleared, "n1") == 0
    assert attempts_so_far(cleared, "n2") == 1
    # clearing the last one drops the container entirely
    fully = clear_attempts(clear_attempts(data, "n1"), "n2")
    assert "_retries" not in (fully or {})


def test_clear_attempts_noop_when_absent() -> None:
    data = {"x": 1}
    assert clear_attempts(data, "n1") is data  # unchanged object
    assert clear_attempts(None, "n1") is None
