"""Unit tests for the Clerk JWKS verifier (parity with the Go api-go path).

Self-contained: an in-test RSA keypair signs tokens and a monkeypatched JWKS
stands in for Clerk's network endpoint. Mirrors the Go middleware test cases.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from api.auth import clerk
from api.auth.clerk import validate_clerk_token
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwk, jwt

ISSUER = "https://clerk.example.com"
KID = "test-key-id"
ALLOWED = ["http://localhost:3000"]


def _priv_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _patch_jwks(monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey) -> None:
    pub_pem = (
        rsa_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    pub: dict[str, Any] = jwk.construct(pub_pem, "RS256").to_dict()
    pub = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in pub.items()}
    pub["kid"] = KID
    pub["use"] = "sig"
    jwks = {"keys": [pub]}

    async def _fake_get_clerk_jwks(issuer: str) -> dict[str, Any]:
        return jwks

    monkeypatch.setattr(clerk, "get_clerk_jwks", _fake_get_clerk_jwks)
    # Reset the module-level cache so cross-test bleed can't occur.
    clerk._jwks_cache = {}
    clerk._jwks_cache_expiry = 0.0


def _sign(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "sub": "user_2abc",
        "iss": ISSUER,
        "azp": "http://localhost:3000",
        "email": "alice@example.com",
        "username": "alice",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not _OMIT}
    return jwt.encode(claims, _priv_pem(key), algorithm="RS256", headers={"kid": KID})


_OMIT = object()


async def test_valid_token_authenticates(rsa_key: rsa.RSAPrivateKey) -> None:
    claims = await validate_clerk_token(_sign(rsa_key), ISSUER, ALLOWED)
    assert claims["sub"] == "user_2abc"
    assert claims["email"] == "alice@example.com"
    assert claims["username"] == "alice"


async def test_rejects_bad_azp(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(JWTError):
        await validate_clerk_token(_sign(rsa_key, azp="http://evil.example.com"), ISSUER, ALLOWED)


async def test_rejects_missing_azp(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(JWTError):
        await validate_clerk_token(_sign(rsa_key, azp=_OMIT), ISSUER, ALLOWED)


async def test_rejects_empty_azp(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(JWTError):
        await validate_clerk_token(_sign(rsa_key, azp=""), ISSUER, ALLOWED)


async def test_rejects_expired(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(JWTError):
        await validate_clerk_token(
            _sign(rsa_key, exp=int(time.time()) - 10, iat=int(time.time()) - 3600),
            ISSUER,
            ALLOWED,
        )


async def test_rejects_wrong_issuer(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(JWTError):
        await validate_clerk_token(_sign(rsa_key, iss="https://attacker.example.com"), ISSUER, ALLOWED)


async def test_rejects_future_nbf(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(JWTError):
        await validate_clerk_token(_sign(rsa_key, nbf=int(time.time()) + 3600), ISSUER, ALLOWED)


async def test_rejects_bad_signature(rsa_key: rsa.RSAPrivateKey) -> None:
    # Sign with a different key whose public half is NOT in the JWKS.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(JWTError):
        await validate_clerk_token(_sign(other), ISSUER, ALLOWED)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _forge(header: dict[str, Any], claims: dict[str, Any], signature: bytes) -> str:
    """Hand-assemble a JWT, bypassing jose's client-side signing guards — this is
    what an attacker who does NOT use our library does."""
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return f"{h}.{p}.{_b64url(signature)}"


def _attack_claims() -> dict[str, Any]:
    return {
        "sub": "user_2abc",
        "iss": ISSUER,
        "azp": "http://localhost:3000",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }


async def test_rejects_alg_none() -> None:
    """An unsigned (alg:none) token must be rejected — the `algorithms=["RS256"]`
    pin in clerk.py is the guard, so this locks it against a future widening.
    Forged by hand because jose refuses to emit `none` tokens."""
    token = _forge({"alg": "none", "kid": KID, "typ": "JWT"}, _attack_claims(), b"")
    with pytest.raises(JWTError):
        await validate_clerk_token(token, ISSUER, ALLOWED)


async def test_rejects_hs256_confusion(rsa_key: rsa.RSAPrivateKey) -> None:
    """Canonical RS256→HS256 downgrade: the attacker HMAC-signs with the (public)
    RSA key bytes as the shared secret. Rejected because decode pins RS256 and
    never treats the key as an HMAC secret. Mirrors the Go HS256-confusion
    negative. Forged by hand because jose refuses to HMAC-sign with a public key."""
    pub_pem = (
        rsa_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    header = {"alg": "HS256", "kid": KID, "typ": "JWT"}
    claims = _attack_claims()
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(pub_pem.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    token = f"{h}.{p}.{_b64url(sig)}"
    with pytest.raises(JWTError):
        await validate_clerk_token(token, ISSUER, ALLOWED)


async def test_get_current_user_rejects_missing_sub(
    rsa_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_current_user must 401 when the verified token carries no `sub` —
    the Python mirror of Go's fail-closed identity contract (auth.go missing-sub).
    The verify step is stubbed so this isolates the post-verify sub enforcement
    (dependencies.py). A missing sub must never reach user provisioning."""
    from api.auth import dependencies
    from api.config import Settings
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    async def _fake_verify(token: str, settings: Settings) -> dict[str, Any]:
        # Valid-looking claims but no `sub`.
        return {"azp": "http://localhost:3000", "email": "alice@example.com"}

    monkeypatch.setattr(dependencies, "_verify_bearer_token", _fake_verify)

    settings = Settings(secret_key="x", clerk_jwt_issuer=ISSUER, clerk_allowed_azp="http://localhost:3000")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    with pytest.raises(HTTPException) as exc:
        # session is never reached — the sub check precedes provisioning.
        await dependencies.get_current_user(settings=settings, session=None, credentials=creds)  # type: ignore[arg-type]
    assert exc.value.status_code == 401
    assert exc.value.detail == "Token missing subject claim"


def test_config_requires_azp_when_clerk_enabled() -> None:
    """Mirrors Go's ErrMissingClerkAllowedAZP fail-fast (security LOW-1)."""
    from api.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(secret_key="x", clerk_jwt_issuer=ISSUER, clerk_allowed_azp="")
    # With an allowlist it constructs fine.
    s = Settings(secret_key="x", clerk_jwt_issuer=ISSUER, clerk_allowed_azp="http://localhost:3000")
    assert s.clerk_allowed_azp_list == ["http://localhost:3000"]
