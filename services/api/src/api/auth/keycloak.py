"""Keycloak JWKS-based JWT validation."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

_jwks_cache: dict[str, Any] = {}
_jwks_cache_expiry: float = 0
_CACHE_TTL_SECONDS = 300


async def get_jwks(keycloak_url: str, realm: str) -> dict[str, Any]:
    """Fetch and cache JWKS from Keycloak."""
    global _jwks_cache, _jwks_cache_expiry

    if _jwks_cache and time.time() < _jwks_cache_expiry:
        return _jwks_cache

    jwks_url = f"{keycloak_url}/realms/{realm}/protocol/openid-connect/certs"
    async with httpx.AsyncClient() as client:
        response = await client.get(jwks_url, timeout=10)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_cache_expiry = time.time() + _CACHE_TTL_SECONDS

    return _jwks_cache


async def validate_keycloak_token(
    token: str,
    keycloak_url: str,
    realm: str,
    client_id: str,
) -> dict[str, Any]:
    """Validate a Keycloak JWT and return the decoded claims.

    Raises JWTError if the token is invalid.
    """
    jwks = await get_jwks(keycloak_url, realm)

    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    matching_key = None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            matching_key = key
            break

    if not matching_key:
        msg = "No matching key found in JWKS"
        raise JWTError(msg)

    claims = jwt.decode(
        token,
        matching_key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=f"{keycloak_url}/realms/{realm}",
    )

    return claims
