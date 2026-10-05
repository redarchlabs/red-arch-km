"""What ``BrainAPIClient`` sends for retrieval scope.

brain-api reads an absent/null ``access_keys`` or ``folder_tags`` as "no filter"
and an explicit ``[]`` as "nothing is readable". So ``None`` must travel as null
(an org admin, an org-wide key, a trusted workflow) and ``[]`` as ``[]`` — the old
``or []`` turned every unrestricted call into an empty list.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from api.services.brain_client import BrainAPIClient

pytestmark = pytest.mark.unit


class _Settings:
    brain_api_url = "http://brain"
    brain_api_key = "k"


@pytest.fixture
def sent() -> Any:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"hits": [], "answer": "", "sources": []})

    real = httpx.AsyncClient

    def _client(*_a: Any, **kw: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), timeout=kw.get("timeout"))

    with patch("api.services.brain_client.httpx.AsyncClient", _client):
        yield bodies


@pytest.mark.parametrize("method", ["vector_search", "vector_chat"])
async def test_none_travels_as_null(sent: list[dict[str, Any]], method: str) -> None:
    await getattr(BrainAPIClient(_Settings()), method)(tenant_id="t", query="q")  # type: ignore[arg-type]
    assert sent[0]["access_keys"] is None
    assert sent[0]["folder_tags"] is None
    assert sent[0]["tags"] == []


@pytest.mark.parametrize("method", ["vector_search", "vector_chat"])
async def test_empty_travels_as_empty(sent: list[dict[str, Any]], method: str) -> None:
    await getattr(BrainAPIClient(_Settings()), method)(  # type: ignore[arg-type]
        tenant_id="t", query="q", access_keys=[], folder_tags=[]
    )
    assert sent[0]["access_keys"] == []
    assert sent[0]["folder_tags"] == []


async def test_agent_ask_none_travels_as_null(sent: list[dict[str, Any]]) -> None:
    await BrainAPIClient(_Settings()).agent_ask(tenant_id="t", query="q")  # type: ignore[arg-type]
    assert sent[0]["access_keys"] is None
