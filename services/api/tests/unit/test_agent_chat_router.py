"""Unit tests for the agentic chat gateway endpoints (brain-api client mocked)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from api.auth.dependencies import CurrentUser, OrgContext
from api.config import Settings
from api.models.user import UserOrgMembership
from api.routers import search as search_router
from api.schemas.search import AgentChatRequest


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        brain_api_url="http://brain-api:8000",
        brain_api_key="test-key",
    )


@pytest.fixture
def admin_ctx() -> OrgContext:
    # Admin → _get_user_access_keys short-circuits to None (no DB access needed).
    user = CurrentUser(
        sub="s", username="admin", email="a@example.com", profile_id=uuid.uuid4(), is_site_admin=False
    )
    return OrgContext(user=user, org_id=uuid.uuid4(), membership=MagicMock(spec=UserOrgMembership), is_org_admin=True)


class FakeClient:
    def __init__(self, settings: Settings) -> None:
        self.calls: dict[str, Any] = {}

    async def agent_ask(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["agent_ask"] = kwargs
        return {
            "answer": "Acme is in Paris [E1].",
            "citations": ["E1"],
            "unsupported_citations": [],
            "evidence": [{"id": "E1"}],
            "iterations": 2,
        }

    async def agent_ask_stream(self, **kwargs: Any) -> AsyncIterator[bytes]:
        self.calls["agent_ask_stream"] = kwargs
        yield b'data: {"type": "thought", "content": "look"}\n\n'
        yield b'data: {"type": "final", "answer": "Paris [E1]"}\n\n'


@pytest.mark.asyncio
async def test_agent_chat_returns_grounded_answer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, admin_ctx: OrgContext
) -> None:
    monkeypatch.setattr(search_router, "BrainAPIClient", FakeClient)
    body = AgentChatRequest(query="Where is Acme HQ?", tags=["policy"])

    result = await search_router.agent_chat(body, ctx=admin_ctx, session=MagicMock(), settings=settings)

    assert result.answer == "Acme is in Paris [E1]."
    assert result.citations == ["E1"]
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_agent_chat_passes_tenant_and_admin_access(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, admin_ctx: OrgContext
) -> None:
    captured: dict[str, Any] = {}

    class Capturing(FakeClient):
        async def agent_ask(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return await super().agent_ask(**kwargs)

    monkeypatch.setattr(search_router, "BrainAPIClient", Capturing)
    await search_router.agent_chat(
        AgentChatRequest(query="q"), ctx=admin_ctx, session=MagicMock(), settings=settings
    )
    assert captured["tenant_id"] == str(admin_ctx.org_id)
    assert captured["access_keys"] is None  # admin → unrestricted


@pytest.mark.asyncio
async def test_agent_chat_stream_forwards_sse(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, admin_ctx: OrgContext
) -> None:
    monkeypatch.setattr(search_router, "BrainAPIClient", FakeClient)
    response = await search_router.agent_chat_stream(
        AgentChatRequest(query="q"), ctx=admin_ctx, session=MagicMock(), settings=settings
    )
    body = "".join([chunk.decode() async for chunk in response.body_iterator])
    assert '"type": "thought"' in body
    assert '"type": "final"' in body
