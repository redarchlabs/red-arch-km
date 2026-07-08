"""Unit tests for the inbound-webhook HMAC signature scheme."""

from __future__ import annotations

import pytest

from api.services.workflow.webhook_signing import (
    DEFAULT_TOLERANCE_SECONDS,
    SignatureError,
    sign,
    verify,
)

pytestmark = pytest.mark.unit

SECRET = "whsec_" + "a" * 40
BODY = b'{"text":"tell me about mars"}'
NOW = 1_700_000_000


def test_sign_then_verify_roundtrips() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)
    # Same secret + body + a clock within tolerance → verifies.
    verify(SECRET, BODY, header, now=NOW)
    verify(SECRET, BODY, header, now=NOW + DEFAULT_TOLERANCE_SECONDS)


def test_header_shape() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)
    assert header.startswith(f"t={NOW},v1=")
    assert len(header.split("v1=")[1]) == 64  # sha256 hex


def test_tampered_body_fails() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="mismatch"):
        verify(SECRET, b'{"text":"transfer all funds"}', header, now=NOW)


def test_wrong_secret_fails() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="mismatch"):
        verify("whsec_" + "b" * 40, BODY, header, now=NOW)


def test_stale_timestamp_fails() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="tolerance"):
        verify(SECRET, BODY, header, now=NOW + DEFAULT_TOLERANCE_SECONDS + 1)


def test_future_timestamp_fails() -> None:
    header = sign(SECRET, BODY, timestamp=NOW)
    with pytest.raises(SignatureError, match="tolerance"):
        verify(SECRET, BODY, header, now=NOW - DEFAULT_TOLERANCE_SECONDS - 1)


def test_missing_header_fails() -> None:
    with pytest.raises(SignatureError, match="missing"):
        verify(SECRET, BODY, None, now=NOW)
    with pytest.raises(SignatureError, match="missing"):
        verify(SECRET, BODY, "", now=NOW)


@pytest.mark.parametrize("header", ["v1=deadbeef", "t=abc,v1=deadbeef", "t=123", "garbage", "t=123,v1="])
def test_malformed_header_fails(header: str) -> None:
    with pytest.raises(SignatureError):
        verify(SECRET, BODY, header, now=NOW)


def test_replay_of_captured_request_eventually_fails() -> None:
    """A valid header captured off the wire stops verifying once its timestamp
    ages past the tolerance window — bounding the replay window."""
    header = sign(SECRET, BODY, timestamp=NOW)
    verify(SECRET, BODY, header, now=NOW + 10)  # fresh replay: still ok
    with pytest.raises(SignatureError, match="tolerance"):
        verify(SECRET, BODY, header, now=NOW + DEFAULT_TOLERANCE_SECONDS + 60)
