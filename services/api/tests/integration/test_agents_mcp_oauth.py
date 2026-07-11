"""Integration tests for MCP OAuth token storage + refresh (org + user identity)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.models.mcp_server import McpServer
from api.models.org import Org
from api.models.user import UserProfile
from api.services.agents.mcp import oauth
from api.services.agents.mcp.oauth_service import McpOAuthService
from api.services.crypto import decrypt_secret
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

os.environ.setdefault("API_SECRET_KEY", "test-secret")
_KEY = "dev-insecure-org-encryption-key-change-me"


def _settings():
    from api.config import get_settings

    return get_settings()


async def _seed(admin_session: AsyncSession, identity: str) -> tuple[Org, McpServer, UserProfile]:
    org = Org(name=f"oauth-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.flush()
    user = UserProfile(
        auth_subject=f"s-{uuid.uuid4()}", username=f"u{uuid.uuid4().hex[:8]}",
        email=f"u{uuid.uuid4().hex[:6]}@t.local",
    )
    admin_session.add(user)
    server = McpServer(
        name="linear", transport="sse", url="https://mcp.example.com/sse",
        config={"auth_type": "oauth", "oauth": {"token_endpoint": "https://a/token", "client_id": "cid"}},
        oauth_identity=identity, org_id=org.id,
    )
    admin_session.add(server)
    await admin_session.commit()
    return org, server, user


async def test_org_identity_store_and_use(admin_session: AsyncSession) -> None:
    org, server, _user = await _seed(admin_session, "org")
    svc = McpOAuthService(admin_session, org.id, _settings())
    await svc.store_exchanged(server, None, oauth.OAuthTokens("access-1", "refresh-1", expires_in=3600))
    await admin_session.commit()

    assert server.oauth_access_token_encrypted != "access-1"  # encrypted at rest
    assert decrypt_secret(server.oauth_access_token_encrypted, _KEY) == "access-1"
    # Fresh token → returned without a refresh.
    assert await svc.ensure_access_token(server, None) == "access-1"


async def test_refresh_when_expired(admin_session: AsyncSession, monkeypatch) -> None:
    org, server, _user = await _seed(admin_session, "org")
    svc = McpOAuthService(admin_session, org.id, _settings())
    await svc.store_exchanged(server, None, oauth.OAuthTokens("access-1", "refresh-1", expires_in=3600))
    server.oauth_token_expires_at = datetime.now(UTC) - timedelta(seconds=10)  # force expiry
    await admin_session.commit()

    async def _fake_refresh(client, token_endpoint, *, refresh_token, client_id, client_secret):
        assert refresh_token == "refresh-1"
        return oauth.OAuthTokens("access-2", "refresh-2", expires_in=3600)

    monkeypatch.setattr(oauth, "refresh_tokens", _fake_refresh)
    assert await svc.ensure_access_token(server, None) == "access-2"
    assert decrypt_secret(server.oauth_access_token_encrypted, _KEY) == "access-2"


async def test_user_identity_is_per_user(admin_session: AsyncSession) -> None:
    org, server, user = await _seed(admin_session, "user")
    other = UserProfile(auth_subject=f"s-{uuid.uuid4()}", username=f"o{uuid.uuid4().hex[:8]}", email="o@t.local")
    admin_session.add(other)
    await admin_session.commit()
    svc = McpOAuthService(admin_session, org.id, _settings())

    await svc.store_exchanged(server, user.id, oauth.OAuthTokens("user-access", "user-refresh", expires_in=3600))
    await admin_session.commit()

    assert await svc.ensure_access_token(server, user.id) == "user-access"
    assert await svc.ensure_access_token(server, other.id) is None  # other user hasn't connected
    assert await svc.ensure_access_token(server, None) is None  # user-mode needs a user
