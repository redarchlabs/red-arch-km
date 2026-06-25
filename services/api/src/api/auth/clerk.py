"""Clerk JWKS-based JWT validation (parity with the Go api-go verifier).

Mirrors `services/api-go/internal/middleware/auth.go`:
- RS256, JWKS fetched from ``{issuer}/.well-known/jwks.json`` and cached.
- Issuer pinned to the configured Clerk Frontend API URL.
- Clerk default session tokens carry NO ``aud``; the security-critical
  replacement is enforcing ``azp`` against an allowlist (G-AZP / R2). The check
  is *default-deny*: ``azp`` must be present AND a member of the allowlist.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

_jwks_cache: dict[str, Any] = {}
_jwks_cache_expiry: float = 0.0
_CACHE_TTL_SECONDS = 300


async def get_clerk_jwks(issuer: str) -> dict[str, Any]:
    """Fetch and cache the Clerk JWKS for the given issuer."""
    global _jwks_cache, _jwks_cache_expiry

    if _jwks_cache and time.time() < _jwks_cache_expiry:
        return _jwks_cache

    jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        response = await client.get(jwks_url, timeout=10)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_cache_expiry = time.time() + _CACHE_TTL_SECONDS

    return _jwks_cache


async def validate_clerk_token(
    token: str,
    issuer: str,
    allowed_azp: list[str],
) -> dict[str, Any]:
    """Validate a Clerk JWT and return the decoded claims.

    Raises JWTError if the token is invalid, including when ``azp`` is missing
    or not in ``allowed_azp`` (anti token-origin-confusion, G-AZP).
    """
    pinned_issuer = issuer.rstrip("/")
    jwks = await get_clerk_jwks(pinned_issuer)

    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    matching_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            matching_key = key
            break

    if not matching_key:
        msg = "No matching key found in Clerk JWKS"
        raise JWTError(msg)

    claims: dict[str, Any] = jwt.decode(
        token,
        matching_key,
        algorithms=["RS256"],
        issuer=pinned_issuer,
        # Clerk default session tokens have no `aud`; skip aud, enforce azp below.
        options={"verify_aud": False},
    )

    # G-AZP (R2): default-deny authorized-party allowlist. Mirrors the Go
    # verifier's checkAuthorizedParty — a Clerk token with no/empty/foreign azp
    # is rejected.
    azp = claims.get("azp")
    if not isinstance(azp, str) or not azp or azp not in allowed_azp:
        msg = "Token azp is missing or not an authorized party"
        raise JWTError(msg)

    return claims
