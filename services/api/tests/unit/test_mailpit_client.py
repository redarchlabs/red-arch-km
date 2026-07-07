"""Unit tests for MailpitClient's transport + error mapping."""

from __future__ import annotations

import httpx
import pytest
from api.config import Settings
from api.services.mailpit_client import MailpitClient, MailpitUnavailableError


def _client_with_handler(handler) -> MailpitClient:  # noqa: ANN001
    """Build a MailpitClient whose httpx.AsyncClient uses a mock transport.

    We patch httpx.AsyncClient at the module level so the client under test
    picks up the mock transport without changing its own construction.
    """
    transport = httpx.MockTransport(handler)
    client = MailpitClient(Settings(secret_key="x"))
    # Swap in a factory that injects our transport (matches how the client
    # opens `httpx.AsyncClient(timeout=...)` per call).
    import api.services.mailpit_client as mod

    orig = httpx.AsyncClient

    def _factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    mod.httpx.AsyncClient = _factory  # type: ignore[assignment]
    return client


@pytest.fixture(autouse=True)
def _restore_httpx() -> None:
    import api.services.mailpit_client as mod

    orig = mod.httpx.AsyncClient
    yield
    mod.httpx.AsyncClient = orig


async def test_list_messages_returns_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/messages"
        return httpx.Response(200, json={"total": 1, "messages": [{"ID": "a"}]})

    client = _client_with_handler(handler)
    payload = await client.list_messages(start=0, limit=10)
    assert payload["total"] == 1


async def test_connect_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client_with_handler(handler)
    with pytest.raises(MailpitUnavailableError):
        await client.list_messages()


async def test_get_message_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"Error": "not found"})

    client = _client_with_handler(handler)
    assert await client.get_message("missing") is None


async def test_get_message_500_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"Error": "boom"})

    client = _client_with_handler(handler)
    with pytest.raises(MailpitUnavailableError):
        await client.get_message("abc")
