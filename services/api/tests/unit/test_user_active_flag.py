"""Unit tests for the user_profiles.is_active flag (site-admin console, Slice 7).

Deactivated users must be rejected at authentication time on BOTH auth paths
(Clerk JWT and the E2E header bypass) — a valid Clerk JWT alone must not grant
access once an admin has deactivated the account. Enforcement lives in
``api.auth.dependencies`` so the provisioning service stays HTTP-free.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.auth import dependencies
from api.config import Settings
from api.models.user import UserProfile
from api.schemas.user import UserRead
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

ISSUER = "https://clerk.example.com"


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "secret_key": "x",
        "clerk_jwt_issuer": ISSUER,
        "clerk_allowed_azp": "http://localhost:3000",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _profile(*, is_active: bool) -> UserProfile:
    return UserProfile(
        id=uuid.uuid4(),
        auth_subject="user_clerk_abc",
        username="alice",
        email="alice@example.com",
        is_site_admin=False,
        is_active=is_active,
    )


def test_user_profile_has_is_active_column_defaulting_true() -> None:
    col = UserProfile.__table__.columns["is_active"]
    assert col.default.arg is True
    # server_default keeps existing rows active when migration 004 backfills.
    assert col.server_default is not None


def test_user_read_exposes_is_active() -> None:
    read = UserRead.model_validate(_profile(is_active=False))
    assert read.is_active is False


def test_ensure_active_passes_for_active_profile() -> None:
    dependencies._ensure_active(_profile(is_active=True))


def test_ensure_active_rejects_inactive_profile() -> None:
    with pytest.raises(HTTPException) as exc:
        dependencies._ensure_active(_profile(is_active=False))
    assert exc.value.status_code == 403
    assert exc.value.detail == "Account is deactivated"


async def test_clerk_path_rejects_inactive_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """A syntactically valid Clerk JWT must not authenticate a deactivated user."""

    async def _fake_verify(token: str, settings: Settings) -> dict[str, Any]:
        return {"sub": "user_clerk_abc", "username": "alice", "email": "alice@example.com"}

    async def _fake_provision(session: Any, *, sub: str, username: str, email: str) -> UserProfile:
        return _profile(is_active=False)

    monkeypatch.setattr(dependencies, "_verify_bearer_token", _fake_verify)
    monkeypatch.setattr(dependencies, "provision_user_from_claims", _fake_provision)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with pytest.raises(HTTPException) as exc:
        await dependencies.get_current_user(settings=_settings(), session=None, credentials=creds)  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert exc.value.detail == "Account is deactivated"


async def test_e2e_path_rejects_inactive_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """The X-Test-User bypass must honor deactivation the same way."""

    async def _fake_provision(session: Any, *, sub: str, username: str, email: str) -> UserProfile:
        return _profile(is_active=False)

    monkeypatch.setattr(dependencies, "provision_user_from_claims", _fake_provision)

    settings = _settings(e2e_test_mode=True, e2e_test_secret="sekrit")
    with pytest.raises(HTTPException) as exc:
        await dependencies.get_current_user(
            settings=settings,
            session=None,  # type: ignore[arg-type]
            credentials=None,
            x_test_user="alice:alice@e2e.local",
            x_test_secret="sekrit",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Account is deactivated"


async def test_clerk_path_accepts_active_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_verify(token: str, settings: Settings) -> dict[str, Any]:
        return {"sub": "user_clerk_abc", "username": "alice", "email": "alice@example.com"}

    profile = _profile(is_active=True)

    async def _fake_provision(session: Any, *, sub: str, username: str, email: str) -> UserProfile:
        return profile

    monkeypatch.setattr(dependencies, "_verify_bearer_token", _fake_verify)
    monkeypatch.setattr(dependencies, "provision_user_from_claims", _fake_provision)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    user = await dependencies.get_current_user(settings=_settings(), session=None, credentials=creds)  # type: ignore[arg-type]
    assert user.profile_id == profile.id
