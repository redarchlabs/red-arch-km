"""Orchestrates the MCP OAuth flow against the DB: start, callback, refresh.

Identity modes:
* ``org``  — one shared install; tokens live on the ``mcp_servers`` row.
* ``user`` — one token per user; tokens live in ``mcp_server_user_tokens``.

The client registration (client_id/secret, discovered endpoints, scopes) is shared
on the server row/config regardless of identity — only the *token* differs. All
secrets + tokens are Fernet-encrypted; a valid access token is minted on demand at
MCP call time (refreshing when it is within 60s of expiry).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.mcp_server import McpOAuthFlow, McpServer, McpServerUserToken
from api.services.agents.mcp import oauth
from api.services.agents.mcp.client import assert_public_host
from api.services.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

_REFRESH_SKEW = timedelta(seconds=60)


class McpOAuthError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _TokenSlot:
    """Where the current identity's tokens live (server row or a user-token row)."""

    access_encrypted: str | None
    refresh_encrypted: str | None
    expires_at: datetime | None
    set_tokens: object  # callable(access_enc, refresh_enc, expires_at) -> None


def _guard(settings: Settings, url: str | None) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    assert_public_host(parsed.hostname or "", parsed.scheme, settings)


def redirect_uri(settings: Settings) -> str:
    return f"{settings.api_public_url.rstrip('/')}/api/agents/mcp-servers/oauth/callback"


def oauth_status(server: McpServer, user_token: McpServerUserToken | None) -> dict:
    """Connection status for a read schema (never exposes tokens)."""
    if (server.config or {}).get("auth_type") != "oauth":
        return {"oauth": False}
    if server.oauth_identity == "user":
        connected = user_token is not None and user_token.access_token_encrypted is not None
        expires = user_token.token_expires_at if user_token else None
    else:
        connected = server.oauth_access_token_encrypted is not None
        expires = server.oauth_token_expires_at
    return {"oauth": True, "identity": server.oauth_identity, "connected": connected, "expires_at": expires}


class McpOAuthService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, settings: Settings) -> None:
        self._session = session
        self._org_id = org_id
        self._settings = settings
        self._key = settings.org_encryption_key.get_secret_value()

    async def start(self, server: McpServer, user_profile_id: uuid.UUID | None) -> str:
        """Discover + register (if needed), then return the provider authorization URL."""
        _guard(self._settings, server.url)
        cfg = dict(server.config or {})
        oc = dict(cfg.get("oauth") or {})
        uri = redirect_uri(self._settings)

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            if not oc.get("authorization_endpoint") or not oc.get("token_endpoint"):
                endpoints = await oauth.discover_endpoints(client, server.url)
                oc["authorization_endpoint"] = endpoints.authorization_endpoint
                oc["token_endpoint"] = endpoints.token_endpoint
                oc["registration_endpoint"] = endpoints.registration_endpoint
            else:
                endpoints = oauth.OAuthEndpoints(
                    oc["authorization_endpoint"], oc["token_endpoint"], oc.get("registration_endpoint")
                )
            if not oc.get("client_id"):
                if not endpoints.registration_endpoint:
                    raise McpOAuthError(
                        "This server has no dynamic client registration; set a pre-registered "
                        "client_id (and secret) in the connection config."
                    )
                client_id, client_secret = await oauth.register_client(
                    client, endpoints.registration_endpoint, redirect_uri=uri, client_name="KM2 Agents"
                )
                oc["client_id"] = client_id
                if client_secret:
                    server.oauth_client_secret_encrypted = encrypt_secret(client_secret, self._key)

        verifier, challenge = oauth.generate_pkce()
        state = oauth.random_state()
        self._session.add(
            McpOAuthFlow(
                mcp_server_id=server.id, user_profile_id=user_profile_id, state=state,
                code_verifier=verifier, redirect_uri=uri, org_id=self._org_id,
            )
        )
        cfg["oauth"] = oc
        server.config = cfg
        await self._session.flush()
        return oauth.build_authorization_url(
            endpoints, client_id=oc["client_id"], redirect_uri=uri, state=state,
            code_challenge=challenge, scope=oc.get("scopes"), resource=server.url,
        )

    async def _slot(self, server: McpServer, user_profile_id: uuid.UUID | None) -> _TokenSlot:
        if server.oauth_identity == "user":
            if user_profile_id is None:
                raise McpOAuthError("this MCP server needs a per-user connection")
            row = (
                await self._session.execute(
                    select(McpServerUserToken).where(
                        McpServerUserToken.mcp_server_id == server.id,
                        McpServerUserToken.user_profile_id == user_profile_id,
                        McpServerUserToken.org_id == self._org_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = McpServerUserToken(
                    mcp_server_id=server.id, user_profile_id=user_profile_id, org_id=self._org_id
                )
                self._session.add(row)

            def _set(a, r, e):
                row.access_token_encrypted = a
                row.refresh_token_encrypted = r
                row.token_expires_at = e

            return _TokenSlot(row.access_token_encrypted, row.refresh_token_encrypted, row.token_expires_at, _set)

        def _set_org(a, r, e):
            server.oauth_access_token_encrypted = a
            server.oauth_refresh_token_encrypted = r
            server.oauth_token_expires_at = e

        return _TokenSlot(
            server.oauth_access_token_encrypted, server.oauth_refresh_token_encrypted,
            server.oauth_token_expires_at, _set_org,
        )

    def _persist(self, slot: _TokenSlot, tokens: oauth.OAuthTokens) -> None:
        expires = _now() + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
        slot.set_tokens(  # type: ignore[operator]
            encrypt_secret(tokens.access_token, self._key),
            encrypt_secret(tokens.refresh_token, self._key) if tokens.refresh_token else None,
            expires,
        )

    async def store_exchanged(
        self, server: McpServer, user_profile_id: uuid.UUID | None, tokens: oauth.OAuthTokens
    ) -> None:
        slot = await self._slot(server, user_profile_id)
        self._persist(slot, tokens)
        await self._session.flush()

    async def ensure_access_token(
        self, server: McpServer, user_profile_id: uuid.UUID | None
    ) -> str | None:
        """A valid access token for this identity, refreshing if near expiry; None if
        the identity has not connected (or can't be refreshed)."""
        try:
            slot = await self._slot(server, user_profile_id)
        except McpOAuthError:
            return None
        if slot.access_encrypted and (slot.expires_at is None or _now() < slot.expires_at - _REFRESH_SKEW):
            return decrypt_secret(slot.access_encrypted, self._key)
        if not slot.refresh_encrypted:
            return None
        oc = (server.config or {}).get("oauth") or {}
        client_secret = (
            decrypt_secret(server.oauth_client_secret_encrypted, self._key)
            if server.oauth_client_secret_encrypted else None
        )
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                tokens = await oauth.refresh_tokens(
                    client, oc["token_endpoint"],
                    refresh_token=decrypt_secret(slot.refresh_encrypted, self._key),
                    client_id=oc.get("client_id"), client_secret=client_secret,
                )
            self._persist(slot, tokens)
            await self._session.flush()
            return tokens.access_token
        except Exception:  # noqa: BLE001 - a failed refresh means "not connected" for this run
            logger.warning("MCP OAuth refresh failed for server %s", server.id)
            return None

    async def disconnect(self, server: McpServer, user_profile_id: uuid.UUID | None) -> None:
        if server.oauth_identity == "user" and user_profile_id is not None:
            row = (
                await self._session.execute(
                    select(McpServerUserToken).where(
                        McpServerUserToken.mcp_server_id == server.id,
                        McpServerUserToken.user_profile_id == user_profile_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                await self._session.delete(row)
        else:
            server.oauth_access_token_encrypted = None
            server.oauth_refresh_token_encrypted = None
            server.oauth_token_expires_at = None
        await self._session.flush()


async def complete_authorization(
    session: AsyncSession, settings: Settings, state: str, code: str
) -> McpServer:
    """Exchange the callback code for tokens. Runs on a PRIVILEGED cross-org session
    (the callback is unauthenticated; the random ``state`` is the capability)."""
    flow = (await session.execute(select(McpOAuthFlow).where(McpOAuthFlow.state == state))).scalar_one_or_none()
    if flow is None:
        raise McpOAuthError("unknown or expired authorization state")
    server = (await session.execute(select(McpServer).where(McpServer.id == flow.mcp_server_id))).scalar_one_or_none()
    if server is None:
        raise McpOAuthError("MCP server no longer exists")
    oc = (server.config or {}).get("oauth") or {}
    key = settings.org_encryption_key.get_secret_value()
    client_secret = decrypt_secret(server.oauth_client_secret_encrypted, key) if server.oauth_client_secret_encrypted else None
    async with httpx.AsyncClient(timeout=20) as client:
        tokens = await oauth.exchange_code(
            client, oc["token_endpoint"], code=code, code_verifier=flow.code_verifier,
            redirect_uri=flow.redirect_uri, client_id=oc.get("client_id"), client_secret=client_secret,
        )
    service = McpOAuthService(session, flow.org_id, settings)
    await service.store_exchanged(server, flow.user_profile_id, tokens)
    await session.delete(flow)
    await session.flush()
    return server
