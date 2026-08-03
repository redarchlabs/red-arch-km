"""Unit tests for request-session lock hardening.

Two guarantees, both learned from the ``user_profiles`` lock storm:

1. Every request session sets ``lock_timeout`` **below** ``statement_timeout``,
   so a request that blocks on a row lock fails fast with a lock error instead
   of pinning a pool connection for the full statement timeout. With
   ``pool_size=10, max_overflow=5``, a handful of 30s waiters starved the whole
   API — unrelated endpoints ran 37-97s.
2. Auth-time provisioning runs in its **own short transaction** that commits
   immediately, rather than on the request-scoped session that stays open until
   the response. Holding a write lock on the user row across the request
   deadlocked the request against its own second connection (``get_tenant_db``),
   which no deadlock detector can break because the holder is idle, not waiting.
"""

from __future__ import annotations

from typing import Any

import pytest
from api import dependencies
from api.config import Settings


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {"secret_key": "x"}
    defaults.update(overrides)
    return Settings(**defaults)


class _RecordingSession:
    """Captures the literal SQL issued during session setup."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, clause: Any, params: Any = None) -> None:
        self.statements.append(str(clause))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _install_factory(monkeypatch: pytest.MonkeyPatch, session: _RecordingSession) -> None:
    monkeypatch.setattr(dependencies, "get_session_factory", lambda _settings: lambda: session)


def _timeout_seconds(statements: list[str], setting: str) -> float:
    """Pull the seconds value out of ``SET LOCAL <setting> = '5s'``."""
    for stmt in statements:
        if setting in stmt:
            raw = stmt.split("=")[1].strip().strip("'\"")
            assert raw.endswith("s"), f"expected a seconds-suffixed value, got {raw!r}"
            return float(raw[:-1])
    raise AssertionError(f"{setting} was never set; statements={statements}")


async def _drain(gen: Any) -> _RecordingSession:
    """Run a session dependency through yield + close."""
    session = await gen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    return session


# --- lock_timeout is set, and is tighter than statement_timeout -------------


async def test_tenant_db_sets_lock_timeout_below_statement_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _RecordingSession()
    _install_factory(monkeypatch, session)

    import uuid

    await _drain(dependencies.get_tenant_db(uuid.uuid4(), _settings()))

    lock = _timeout_seconds(session.statements, "lock_timeout")
    statement = _timeout_seconds(session.statements, "statement_timeout")
    assert lock < statement, "a lock waiter must give up before the statement timeout fires"
    assert session.committed


async def test_get_db_sets_lock_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _RecordingSession()
    _install_factory(monkeypatch, session)

    await _drain(dependencies.get_db(_settings()))

    assert _timeout_seconds(session.statements, "lock_timeout") > 0
    assert session.committed


async def test_lock_timeout_precedes_any_query_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting it after the first statement would leave that statement unguarded."""
    session = _RecordingSession()
    _install_factory(monkeypatch, session)

    import uuid

    await _drain(dependencies.get_tenant_db(uuid.uuid4(), _settings()))

    assert any("lock_timeout" in s for s in session.statements)


# --- provisioning commits in its own short transaction ----------------------


async def test_auth_provisioning_session_commits_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: the write must not outlive this block."""
    session = _RecordingSession()
    _install_factory(monkeypatch, session)

    async with dependencies.auth_provisioning_session(_settings()) as s:
        assert not s.committed, "must still be open inside the block"

    assert session.committed, "provisioning must commit before the request continues"


async def test_auth_provisioning_session_enters_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """user_profiles is RLS-forced and provisioning has no tenant context yet."""
    session = _RecordingSession()
    _install_factory(monkeypatch, session)

    async with dependencies.auth_provisioning_session(_settings()):
        pass

    assert any("app.bypass" in s for s in session.statements)


async def test_auth_provisioning_session_rolls_back_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _RecordingSession()
    _install_factory(monkeypatch, session)

    with pytest.raises(RuntimeError):
        async with dependencies.auth_provisioning_session(_settings()):
            raise RuntimeError("boom")

    assert session.rolled_back
    assert not session.committed
