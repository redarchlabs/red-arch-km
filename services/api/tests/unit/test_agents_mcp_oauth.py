"""Unit tests for the MCP OAuth flow logic (PKCE, URL building, token exchange)."""

from __future__ import annotations

import base64
import hashlib

import pytest

from api.services.agents.mcp import oauth

pytestmark = pytest.mark.unit


def test_generate_pkce_is_s256():
    verifier, challenge = oauth.generate_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge


def test_build_authorization_url_has_pkce_and_resource():
    ep = oauth.OAuthEndpoints("https://auth.example.com/authorize", "https://auth.example.com/token")
    url = oauth.build_authorization_url(
        ep, client_id="cid", redirect_uri="https://km2/cb", state="st",
        code_challenge="chal", scope="read write", resource="https://mcp.example.com/sse",
    )
    assert url.startswith("https://auth.example.com/authorize?")
    for frag in ["client_id=cid", "code_challenge=chal", "code_challenge_method=S256",
                 "state=st", "scope=read+write", "resource=https"]:
        assert frag in url


def test_parse_metadata_requires_endpoints():
    ep = oauth.parse_auth_server_metadata(
        {"authorization_endpoint": "https://a/authorize", "token_endpoint": "https://a/token",
         "registration_endpoint": "https://a/register"}
    )
    assert ep.registration_endpoint == "https://a/register"
    with pytest.raises(oauth.OAuthError):
        oauth.parse_auth_server_metadata({"authorization_endpoint": "https://a/authorize"})


class _Resp:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._data


class _PostClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def post(self, url, data=None, json=None, auth=None):
        self.calls.append({"url": url, "data": data, "json": json, "auth": auth})
        return self._resp


@pytest.mark.asyncio
async def test_exchange_code_public_client_puts_client_id_in_body():
    client = _PostClient(_Resp(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}))
    tokens = await oauth.exchange_code(
        client, "https://a/token", code="c", code_verifier="v",
        redirect_uri="https://km2/cb", client_id="cid", client_secret=None,
    )
    assert tokens.access_token == "at" and tokens.refresh_token == "rt" and tokens.expires_in == 3600
    assert client.calls[0]["auth"] is None
    assert client.calls[0]["data"]["client_id"] == "cid"  # public client → id in body


@pytest.mark.asyncio
async def test_exchange_code_confidential_uses_basic_auth():
    client = _PostClient(_Resp(200, {"access_token": "at"}))
    await oauth.exchange_code(
        client, "https://a/token", code="c", code_verifier="v",
        redirect_uri="https://km2/cb", client_id="cid", client_secret="sec",
    )
    assert client.calls[0]["auth"] == ("cid", "sec")
    assert "client_id" not in client.calls[0]["data"]


@pytest.mark.asyncio
async def test_refresh_keeps_prior_refresh_token_when_omitted():
    client = _PostClient(_Resp(200, {"access_token": "new", "expires_in": 3600}))
    tokens = await oauth.refresh_tokens(
        client, "https://a/token", refresh_token="old-rt", client_id="cid", client_secret=None,
    )
    assert tokens.access_token == "new"
    assert tokens.refresh_token == "old-rt"  # server omitted it → keep the old one


@pytest.mark.asyncio
async def test_register_client_returns_id_and_secret():
    client = _PostClient(_Resp(201, {"client_id": "cid", "client_secret": "sec"}))
    cid, secret = await oauth.register_client(
        client, "https://a/register", redirect_uri="https://km2/cb", client_name="KM2",
    )
    assert (cid, secret) == ("cid", "sec")


@pytest.mark.asyncio
async def test_token_error_on_non_200():
    client = _PostClient(_Resp(400, text="bad_grant"))
    with pytest.raises(oauth.OAuthError):
        await oauth.refresh_tokens(client, "https://a/token", refresh_token="x", client_id="c", client_secret=None)
