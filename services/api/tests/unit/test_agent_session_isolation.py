"""Unit tests for AgentService per-tool transaction isolation (Finding 2).

Proves that each tool invocation runs in its OWN short-lived, tenant-scoped
session that is committed per tool, and that a failure in one tool rolls back
only that tool — a prior successful tool's write stays committed. The DB session
factory and the OpenAI client are faked, so no DB or LLM is needed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from api.config import Settings
from api.services.agent import AgentService

ORG_ID = uuid.uuid4()


class FakeSession:
    """Records commit/rollback and the SET-tenant statements it received."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.statements: list[str] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type: Any, *_rest: Any) -> bool:
        # Mirror AsyncSession: an unhandled error closes the session without a
        # commit, discarding (rolling back) the transaction.
        if exc_type is not None:
            self.rolled_back = True
        return False

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        self.statements.append(str(statement))
        return SimpleNamespace()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeFactory:
    """async_sessionmaker stand-in: hands out a fresh FakeSession per call."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


@pytest.fixture
def settings() -> Settings:
    return Settings(
        secret_key="test-secret",
        database_url="postgresql+asyncpg://t:t@localhost/t",
        org_encryption_key="agent-test-key",
    )


def _agent(factory: FakeFactory, settings: Settings) -> AgentService:
    return AgentService(ORG_ID, settings, session_factory=factory, org_openai_key="sk-test")


@pytest.mark.asyncio
async def test_each_tool_runs_in_its_own_committed_session(settings: Settings) -> None:
    factory = FakeFactory()
    agent = _agent(factory, settings)

    async def good(session: FakeSession, _args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    # Instance attribute shadows the class; _dispatch resolves it via getattr.
    agent._tool_good = good  # type: ignore[attr-defined]

    result = await agent._dispatch("good", {})
    assert result == {"ok": True}
    assert len(factory.sessions) == 1
    session = factory.sessions[0]
    assert session.committed is True
    assert session.rolled_back is False
    # Tenant context (GUC) was applied on this per-tool session. Note the agent
    # keeps the privileged role (no SET ROLE app_user) because its tools run
    # schema DDL — see AgentService.apply_tenant_scope.
    assert any("app.current_tenant_id" in s for s in session.statements)
    assert not any("app_user" in s for s in session.statements)


@pytest.mark.asyncio
async def test_tool_failure_rolls_back_only_its_own_session(settings: Settings) -> None:
    factory = FakeFactory()
    agent = _agent(factory, settings)

    async def good(session: FakeSession, _args: dict[str, Any]) -> dict[str, Any]:
        return {"created": True}

    async def bad(session: FakeSession, _args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    agent._tool_good = good  # type: ignore[attr-defined]
    agent._tool_bad = bad  # type: ignore[attr-defined]

    r1 = await agent._dispatch("good", {})
    r2 = await agent._dispatch("bad", {})

    assert r1 == {"created": True}
    assert "error" in r2  # failure surfaced, not raised

    assert len(factory.sessions) == 2
    first, second = factory.sessions
    # Prior successful tool committed and was NOT rolled back by the later failure.
    assert first.committed is True
    assert first.rolled_back is False
    # Failing tool's own session was rolled back and never committed.
    assert second.committed is False
    assert second.rolled_back is True


# --------------------------------------------------------------------------- #
# Full loop with a mocked LLM: two tool calls in one turn each get their own
# committed session; no DB connection is held across the LLM round-trips.
# --------------------------------------------------------------------------- #


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str = "{}") -> None:
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _Message:
    def __init__(self, tool_calls: list[_ToolCall] | None, content: str | None) -> None:
        self.tool_calls = tool_calls
        self.content = content


class _Response:
    def __init__(self, message: _Message) -> None:
        self.choices = [SimpleNamespace(message=message)]


class _FakeCompletions:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.calls = 0

    async def create(self, **_kwargs: Any) -> _Response:
        response = self._responses[self.calls]
        self.calls += 1
        return response


class _FakeOpenAI:
    def __init__(self, responses: list[_Response]) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


@pytest.mark.asyncio
async def test_run_stream_uses_a_fresh_session_per_tool_call(settings: Settings) -> None:
    factory = FakeFactory()
    agent = _agent(factory, settings)

    async def good(session: FakeSession, _args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    agent._tool_alpha = good  # type: ignore[attr-defined]
    agent._tool_beta = good  # type: ignore[attr-defined]

    responses = [
        _Response(_Message([_ToolCall("c1", "alpha"), _ToolCall("c2", "beta")], "")),
        _Response(_Message(None, "done")),
    ]
    agent._client = _FakeOpenAI(responses)  # type: ignore[assignment]

    events = [event async for event in agent.run_stream([{"role": "user", "content": "hi"}])]

    # Two tool calls -> two independent sessions, each committed.
    assert len(factory.sessions) == 2
    assert all(s.committed and not s.rolled_back for s in factory.sessions)
    assert {e["type"] for e in events} >= {"tool_call", "tool_result", "done"}
