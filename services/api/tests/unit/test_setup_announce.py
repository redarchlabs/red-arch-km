"""Unit tests for the boot-time setup-token announcement in main.py.

Its whole contract is a fail-safe: log the token when one is minted, and
NEVER let a broken/hung dependency stop the API from starting.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# api.main builds the FastAPI app at import time, which requires a configured
# environment; provide minimal values before importing it.
os.environ.setdefault("API_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from api import main as main_module  # noqa: E402
from api.config import Settings  # noqa: E402


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = Settings(secret_key="x", setup_token_ttl_seconds=86400)
    monkeypatch.setattr(main_module, "get_settings", lambda: s)
    return s


def _session_factory_stub() -> Any:
    """An async context-manager factory whose session is a plain AsyncMock."""
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


async def test_announce_logs_token_banner(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(main_module, "get_session_factory", lambda s: _session_factory_stub())
    monkeypatch.setattr(main_module, "get_redis_client", lambda s: AsyncMock())

    async def _mint(session: Any, redis: Any, *, ttl_seconds: int) -> str:
        assert ttl_seconds == 86400
        return "tok-plaintext"

    async def _no_orgs(session: Any) -> bool:
        return False

    monkeypatch.setattr(main_module, "ensure_setup_token", _mint)
    monkeypatch.setattr(main_module, "_instance_has_orgs", _no_orgs)

    with caplog.at_level(logging.WARNING, logger="api.main"):
        await main_module._announce_setup_token_if_needed()

    banner = "\n".join(r.getMessage() for r in caplog.records)
    assert "FIRST-RUN SETUP" in banner
    assert "tok-plaintext" in banner
    assert "24h" in banner
    assert "RECOVERY MODE" not in banner


async def test_announce_flags_recovery_mode_on_populated_instance(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(main_module, "get_session_factory", lambda s: _session_factory_stub())
    monkeypatch.setattr(main_module, "get_redis_client", lambda s: AsyncMock())

    async def _mint(session: Any, redis: Any, *, ttl_seconds: int) -> str:
        return "tok-plaintext"

    async def _has_orgs(session: Any) -> bool:
        return True

    monkeypatch.setattr(main_module, "ensure_setup_token", _mint)
    monkeypatch.setattr(main_module, "_instance_has_orgs", _has_orgs)

    with caplog.at_level(logging.WARNING, logger="api.main"):
        await main_module._announce_setup_token_if_needed()

    assert any("RECOVERY MODE" in r.getMessage() for r in caplog.records)


async def test_announce_survives_broken_dependencies(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Redis/DB down at boot must log and continue — never raise."""

    def _boom(s: Settings) -> Any:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(main_module, "get_session_factory", lambda s: _session_factory_stub())
    monkeypatch.setattr(main_module, "get_redis_client", _boom)

    with caplog.at_level(logging.ERROR, logger="api.main"):
        await main_module._announce_setup_token_if_needed()  # must not raise

    assert any("bootstrap check failed" in r.getMessage() for r in caplog.records)


async def test_announce_silent_when_no_token_minted(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(main_module, "get_session_factory", lambda s: _session_factory_stub())
    monkeypatch.setattr(main_module, "get_redis_client", lambda s: AsyncMock())

    async def _none(session: Any, redis: Any, *, ttl_seconds: int) -> None:
        return None

    monkeypatch.setattr(main_module, "ensure_setup_token", _none)

    with caplog.at_level(logging.INFO, logger="api.main"):
        await main_module._announce_setup_token_if_needed()

    assert not any("FIRST-RUN SETUP" in r.getMessage() for r in caplog.records)
