"""HMAC-SHA256 signature scheme for inbound workflow webhooks.

A sender signs each request with a per-endpoint shared secret so the receiver can
verify authenticity + integrity and reject replays — the same scheme Stripe and
GitHub use, so any client can implement it in a few lines. The signature travels
in a header::

    X-KM2-Signature: t=<unix_seconds>,v1=<hex hmac-sha256>

where the signed message is ``f"{t}.{raw_body}"`` over the EXACT raw request
bytes (not the re-serialized JSON, which would not round-trip). The receiver
recomputes the HMAC with the stored secret, compares it in constant time, and
rejects timestamps outside a tolerance window so a captured request can't be
replayed indefinitely.

The secret is high-entropy and shared out-of-band (returned once when the inbound
endpoint is created, stored only Fernet-encrypted). Only a holder of the secret
can produce a request that verifies — so an exposed endpoint URL is not, on its
own, an abuse surface.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# Header carrying the signature. Kept vendor-neutral so any client can send it.
SIGNATURE_HEADER = "X-KM2-Signature"

# How far a request's timestamp may drift from server time before it is rejected
# as a possible replay (seconds). Generous enough for clock skew, tight enough to
# bound a captured-request replay window.
DEFAULT_TOLERANCE_SECONDS = 300

_SCHEME_VERSION = "v1"


class SignatureError(Exception):
    """An inbound webhook signature was missing, malformed, stale, or invalid.

    The message names the failure mode for logs; the HTTP layer maps every
    variant to a single 401 so a caller can't distinguish (no oracle).
    """


def _compute(secret: str, timestamp: int, raw_body: bytes) -> str:
    """HMAC-SHA256 hex of ``"{timestamp}.{raw_body}"`` keyed by ``secret``."""
    signed = str(int(timestamp)).encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def sign(secret: str, raw_body: bytes, *, timestamp: int) -> str:
    """Build the ``X-KM2-Signature`` header value for ``raw_body`` at ``timestamp``.

    Provided so senders (and tests) share one implementation with the verifier.
    """
    return f"t={int(timestamp)},{_SCHEME_VERSION}={_compute(secret, timestamp, raw_body)}"


def _parse_header(header: str) -> tuple[int, str]:
    """Parse ``t=<ts>,v1=<hex>`` (order-insensitive; unknown params ignored)."""
    timestamp: int | None = None
    signature: str | None = None
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise SignatureError("malformed timestamp") from exc
        elif key == _SCHEME_VERSION:
            signature = value.strip()
    if timestamp is None or not signature:
        raise SignatureError("signature header missing t or v1")
    return timestamp, signature


def verify(
    secret: str,
    raw_body: bytes,
    header: str | None,
    *,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> None:
    """Verify ``header`` against ``raw_body`` and ``secret``.

    Returns ``None`` on success; raises :class:`SignatureError` on any failure
    (missing/malformed header, timestamp outside the tolerance window, or HMAC
    mismatch). The comparison is constant-time.
    """
    if not header:
        raise SignatureError("missing signature header")
    timestamp, provided = _parse_header(header)
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > tolerance_seconds:
        raise SignatureError("timestamp outside tolerance window")
    expected = _compute(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, provided):
        raise SignatureError("signature mismatch")
