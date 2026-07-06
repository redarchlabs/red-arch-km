"""Opaque bearer tokens for public form links.

A link's raw token is shown exactly once (at creation / in the emailed URL) and
never persisted — only its SHA-256 hash is stored, so a database read cannot
recover a usable token. Lookups hash the presented token and compare against the
stored hash (indexed, unique).
"""

from __future__ import annotations

import hashlib
import secrets

# 32 bytes of entropy -> ~43 url-safe chars. Ample against guessing.
_TOKEN_BYTES = 32


def generate_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Persist only the hash."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    """SHA-256 hex digest of a raw token (stable, url-safe input)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
