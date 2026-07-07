"""Retry policy + backoff for the token engine (BPMN error-handling phase).

A retryable task failure does NOT sink the run: the engine parks the token as
``waiting/retry`` with a ``resume_at`` in the future, and the existing timer sweep
(``resume_due_tokens``, which already reactivates ``wait_kind='retry'``) brings it
back to re-dispatch the same node — a genuine retry with no new machinery.

Retry is **opt-in** per task via ``node.data.retry`` so legacy graphs (and any v2
task without a policy) keep the fail-fast behaviour they shadow-tested against.
Backoff is **full-jitter exponential** (AWS's recommended scheme): each retry
waits a random duration in ``[0, min(cap, base * 2**attempt)]``, which spreads a
thundering herd of simultaneously-failing runs across the retry window.

Pure functions only (no DB / engine state) so the policy + backoff are trivially
unit-testable; the engine owns the token/step side effects.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

# Guardrails on author-supplied values so a typo can't schedule a runaway or
# absurdly long retry chain. max_attempts includes the first (non-retry) attempt.
_MAX_ATTEMPTS_CAP = 20
_MAX_DELAY_CAP_SECONDS = 24 * 3600  # 1 day
_DEFAULT_BASE_DELAY = 1.0
_DEFAULT_MAX_DELAY = 300.0
_DEFAULT_MAX_ATTEMPTS = 3

# Where per-node attempt counters live on a token's ``data`` JSONB.
_RETRIES_KEY = "_retries"


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to attempt a task, and how long to wait between attempts.

    ``max_attempts == 1`` means *no retry* (the default when a task declares no
    ``retry`` policy) — the task runs once and a failure propagates as before.
    """

    max_attempts: int = 1
    base_delay: float = _DEFAULT_BASE_DELAY
    max_delay: float = _DEFAULT_MAX_DELAY

    @property
    def retries_enabled(self) -> bool:
        return self.max_attempts > 1


def read_policy(node_data: dict[str, Any] | None) -> RetryPolicy:
    """Parse a task node's ``data.retry`` into a clamped :class:`RetryPolicy`.

    Absent / non-dict / empty ``retry`` → ``RetryPolicy()`` (no retry), preserving
    legacy fail-fast semantics. A present policy defaults to 3 attempts with 1s
    base / 300s cap, each value clamped to a sane range.
    """
    spec = (node_data or {}).get("retry")
    if not isinstance(spec, dict) or not spec:
        return RetryPolicy()
    max_attempts = _clamp_int(
        spec.get("max_attempts"), default=_DEFAULT_MAX_ATTEMPTS, low=1, high=_MAX_ATTEMPTS_CAP
    )
    base_delay = _clamp_float(
        spec.get("base_delay_seconds"), default=_DEFAULT_BASE_DELAY, low=0.0, high=_MAX_DELAY_CAP_SECONDS
    )
    max_delay = _clamp_float(
        spec.get("max_delay_seconds"), default=_DEFAULT_MAX_DELAY, low=0.0, high=_MAX_DELAY_CAP_SECONDS
    )
    return RetryPolicy(max_attempts=max_attempts, base_delay=base_delay, max_delay=max(base_delay, max_delay))


def backoff(attempt: int, policy: RetryPolicy) -> float:
    """Full-jitter exponential delay (seconds) before retry ``attempt`` (0-based).

    ``attempt`` is the number of failures so far (0 = compute the delay before the
    2nd try). Returns a value in ``[0, min(max_delay, base_delay * 2**attempt)]``.
    ``base_delay == 0`` yields ``0`` (immediate retry) — handy for deterministic
    tests.
    """
    ceiling = min(policy.max_delay, policy.base_delay * (2 ** max(0, attempt)))
    if ceiling <= 0:
        return 0.0
    # Jitter for backoff spreading, not security — non-crypto RNG is correct here.
    return random.uniform(0.0, ceiling)  # noqa: S311


def attempts_so_far(token_data: dict[str, Any] | None, node_id: str) -> int:
    """How many times this token has already attempted ``node_id`` (0 first time)."""
    retries = (token_data or {}).get(_RETRIES_KEY)
    if not isinstance(retries, dict):
        return 0
    value = retries.get(node_id, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def record_attempt(token_data: dict[str, Any] | None, node_id: str, attempt: int) -> dict[str, Any]:
    """Return a new token-data dict with ``node_id``'s attempt counter set."""
    data = dict(token_data or {})
    retries = dict(data.get(_RETRIES_KEY) or {})
    retries[node_id] = attempt
    data[_RETRIES_KEY] = retries
    return data


def clear_attempts(token_data: dict[str, Any] | None, node_id: str) -> dict[str, Any] | None:
    """Drop ``node_id``'s attempt counter once it succeeds (so a later loop back
    through the same node starts its retry budget fresh). Returns the input
    unchanged when there is nothing to clear."""
    retries = (token_data or {}).get(_RETRIES_KEY)
    if not isinstance(retries, dict) or node_id not in retries:
        return token_data
    data = dict(token_data or {})
    new_retries = {k: v for k, v in retries.items() if k != node_id}
    if new_retries:
        data[_RETRIES_KEY] = new_retries
    else:
        data.pop(_RETRIES_KEY, None)
    return data


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _clamp_float(value: Any, *, default: float, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, float(value)))
