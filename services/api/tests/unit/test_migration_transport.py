"""Unit tests for the outbound promotion transport's SSRF guard (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api.services.migration.transport import OutboundPushClient, TransportError

pytestmark = pytest.mark.unit


def _client(allow: tuple[str, ...] = (), trusted: tuple[str, ...] = ()) -> OutboundPushClient:
    return OutboundPushClient(
        SimpleNamespace(workflow_webhook_allowlist=allow, workflow_trusted_local_hosts=trusted)
    )


def test_rejects_non_allowlisted_host() -> None:
    with pytest.raises(TransportError):
        _client()._guard("https://evil.example.com/api")


def test_requires_https_even_when_allowlisted() -> None:
    # Allow-listed but plain http and not a trusted local host → refused.
    with pytest.raises(TransportError):
        _client(allow=("staging.km2.example.com",))._guard("http://staging.km2.example.com")


def test_allows_allowlisted_https() -> None:
    host, scheme = _client(allow=("staging.km2.example.com",))._guard("https://staging.km2.example.com/x")
    assert host == "staging.km2.example.com"
    assert scheme == "https"


def test_blocks_private_ip_even_if_allowlisted() -> None:
    # Link-local metadata IP: blocked by the private-address check despite allow-list.
    with pytest.raises(TransportError):
        _client(allow=("169.254.169.254",))._guard("https://169.254.169.254/latest/meta-data")


def test_allows_trusted_local_http() -> None:
    host, scheme = _client(trusted=("localhost",))._guard("http://localhost:8001/api")
    assert host == "localhost"
    assert scheme == "http"
