"""OAuth 2.1 client for remote MCP servers (RFC 8414 discovery + RFC 7591 DCR + PKCE).

Hand-rolled rather than using the SDK's interactive ``OAuthClientProvider`` because a
web app must *persist and resume* the flow across a browser redirect, not block on a
local loopback. Network calls take an injected ``httpx.AsyncClient`` so the pure
helpers (PKCE, URL building, metadata parsing) are unit-testable without I/O.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OAuthEndpoints:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"


class OAuthError(RuntimeError):
    pass


# --- pure helpers ----------------------------------------------------------


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def random_state() -> str:
    return secrets.token_urlsafe(24)


def parse_auth_server_metadata(data: dict[str, Any]) -> OAuthEndpoints:
    auth = data.get("authorization_endpoint")
    token = data.get("token_endpoint")
    if not auth or not token:
        raise OAuthError("authorization server metadata missing authorization/token endpoint")
    return OAuthEndpoints(
        authorization_endpoint=auth,
        token_endpoint=token,
        registration_endpoint=data.get("registration_endpoint"),
    )


def build_authorization_url(
    endpoints: OAuthEndpoints,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str | None,
    resource: str | None = None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    if resource:  # RFC 8707 resource indicator (MCP servers expect it)
        params["resource"] = resource
    sep = "&" if "?" in endpoints.authorization_endpoint else "?"
    return f"{endpoints.authorization_endpoint}{sep}{urlencode(params)}"


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _tokens_from_response(data: dict[str, Any]) -> OAuthTokens:
    access = data.get("access_token")
    if not access:
        raise OAuthError("token response missing access_token")
    return OAuthTokens(
        access_token=access,
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        token_type=data.get("token_type", "Bearer"),
    )


# --- network steps ---------------------------------------------------------


async def discover_endpoints(client: httpx.AsyncClient, server_url: str) -> OAuthEndpoints:
    """Resolve the authorization-server metadata for an MCP server URL.

    Tries, in order: the protected-resource metadata (→ its authorization server),
    then the origin's ``oauth-authorization-server`` and ``openid-configuration``
    well-known documents. Raises :class:`OAuthError` if none resolve."""
    origin = _origin(server_url)
    candidates: list[str] = []

    # 1) Protected-resource metadata points at the authorization server(s).
    try:
        pr = await client.get(urljoin(origin + "/", ".well-known/oauth-protected-resource"))
        if pr.status_code == 200:
            servers = pr.json().get("authorization_servers") or []
            for as_url in servers:
                candidates.append(urljoin(as_url.rstrip("/") + "/", ".well-known/oauth-authorization-server"))
                candidates.append(urljoin(as_url.rstrip("/") + "/", ".well-known/openid-configuration"))
                candidates.append(as_url)  # some return the metadata document directly
    except httpx.HTTPError:
        pass

    # 2) Origin-hosted well-known documents.
    candidates.append(urljoin(origin + "/", ".well-known/oauth-authorization-server"))
    candidates.append(urljoin(origin + "/", ".well-known/openid-configuration"))

    for url in candidates:
        try:
            resp = await client.get(url)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                return parse_auth_server_metadata(resp.json())
        except (httpx.HTTPError, OAuthError, ValueError):
            continue
    raise OAuthError(f"could not discover OAuth metadata for {server_url}")


async def register_client(
    client: httpx.AsyncClient,
    registration_endpoint: str,
    *,
    redirect_uri: str,
    client_name: str,
) -> tuple[str, str | None]:
    """Dynamic Client Registration (RFC 7591). Returns (client_id, client_secret?)."""
    body = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    resp = await client.post(registration_endpoint, json=body)
    if resp.status_code not in (200, 201):
        raise OAuthError(f"dynamic client registration failed ({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    client_id = data.get("client_id")
    if not client_id:
        raise OAuthError("registration response missing client_id")
    return client_id, data.get("client_secret")


def _auth_and_body(client_id: str, client_secret: str | None, params: dict[str, str]):
    """Token-endpoint auth: HTTP Basic for confidential clients, else client_id in body."""
    if client_secret:
        return (client_id, client_secret), params
    return None, {**params, "client_id": client_id}


async def exchange_code(
    client: httpx.AsyncClient,
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
) -> OAuthTokens:
    auth, body = _auth_and_body(
        client_id, client_secret,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier},
    )
    resp = await client.post(token_endpoint, data=body, auth=auth)
    if resp.status_code != 200:
        raise OAuthError(f"code exchange failed ({resp.status_code}): {resp.text[:200]}")
    return _tokens_from_response(resp.json())


async def refresh_tokens(
    client: httpx.AsyncClient,
    token_endpoint: str,
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str | None,
) -> OAuthTokens:
    auth, body = _auth_and_body(
        client_id, client_secret,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    resp = await client.post(token_endpoint, data=body, auth=auth)
    if resp.status_code != 200:
        raise OAuthError(f"token refresh failed ({resp.status_code}): {resp.text[:200]}")
    tokens = _tokens_from_response(resp.json())
    # Some servers omit the refresh_token on refresh — keep the prior one.
    if tokens.refresh_token is None:
        tokens = OAuthTokens(tokens.access_token, refresh_token, tokens.expires_in, tokens.token_type)
    return tokens
