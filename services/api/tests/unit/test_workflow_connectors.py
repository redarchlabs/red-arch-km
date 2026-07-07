"""Unit tests for connector auth-header construction (secret handling)."""

from __future__ import annotations

import base64

import pytest
from api.services.workflow.actions import ResolvedConnection, _auth_headers

pytestmark = pytest.mark.unit


def _conn(auth_type: str, secret: str | None, config: dict | None = None) -> ResolvedConnection:
    return ResolvedConnection(
        name="c", base_url="https://api.example.com", auth_type=auth_type, secret=secret, config=config or {}
    )


def test_none_auth_sends_no_headers() -> None:
    assert _auth_headers(None) == {}
    assert _auth_headers(_conn("none", None)) == {}
    assert _auth_headers(_conn("bearer", None)) == {}  # no secret ⇒ nothing


def test_bearer_auth() -> None:
    assert _auth_headers(_conn("bearer", "tok123")) == {"Authorization": "Bearer tok123"}


def test_api_key_auth_default_and_custom_header() -> None:
    assert _auth_headers(_conn("api_key", "k")) == {"X-API-Key": "k"}
    assert _auth_headers(_conn("api_key", "k", {"header": "X-Custom"})) == {"X-Custom": "k"}


def test_basic_auth_base64_encodes_user_and_secret() -> None:
    headers = _auth_headers(_conn("basic", "pw", {"username": "alice"}))
    expected = base64.b64encode(b"alice:pw").decode()
    assert headers == {"Authorization": f"Basic {expected}"}
