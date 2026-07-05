"""First-run setup token: bootstrap trust anchor for claiming site admin.

While no active site admin exists, the first worker to boot generates a
one-time token, logs the plaintext to the server console, and stores only its
SHA-256 hash in Redis (TTL-bound; never overwritten while unclaimed). The
wizard (`/api/setup/claim`) consumes it atomically, so the operator who can
read the server logs — and only them — can claim global admin on a fresh
install (Jupyter/Portainer pattern).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from redis.asyncio import Redis
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import UserProfile

logger = logging.getLogger(__name__)

TOKEN_KEY = "setup:token:hash"  # noqa: S105 — Redis key name, not a credential
LOCK_KEY = "setup:token:lock"
_LOCK_TTL_SECONDS = 15


async def site_admin_exists(session: AsyncSession) -> bool:
    """True when at least one ACTIVE site admin exists (deactivated admins
    don't count — otherwise deactivating the last admin would brick setup)."""
    result = await session.execute(
        select(exists().where(UserProfile.is_site_admin.is_(True), UserProfile.is_active.is_(True)))
    )
    return bool(result.scalar_one())


async def ensure_setup_token(session: AsyncSession, redis: Redis, *, ttl_seconds: int) -> str | None:
    """Ensure the setup token reflects reality at boot.

    Returns the plaintext token when THIS caller generated it (so the caller
    can log it), else None. The lock guarantees exactly one worker per boot
    attempts generation; the NX write below guarantees an existing unclaimed
    token is never overwritten.
    """
    if await site_admin_exists(session):
        await redis.delete(TOKEN_KEY)
        return None

    locked = await redis.set(LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_SECONDS)
    if not locked:
        return None

    # NX: never overwrite an unclaimed token. A worker that boots late (or a
    # crash-looping process past the lock TTL) must not silently invalidate
    # the token the operator already copied from the first worker's logs. The
    # token therefore persists across restarts until claimed or TTL-expired;
    # to force reissue, delete the Redis key or wait out the TTL.
    token = secrets.token_urlsafe(32)
    stored = await redis.set(TOKEN_KEY, hashlib.sha256(token.encode()).hexdigest(), ex=ttl_seconds, nx=True)
    if not stored:
        logger.info("Setup token already issued and unclaimed; it remains valid until its TTL expires")
        return None
    return token


async def consume_setup_token(redis: Redis, candidate: str) -> bool:
    """Validate and atomically consume the token (single use).

    A wrong guess does NOT burn the stored token. Two concurrent correct
    claims race on DELETE — only the one that actually removed the key wins.
    (GET→compare→DEL is not atomic against a concurrent rewrite of the key,
    but nothing rewrites it: generation is SET NX, so the key is immutable
    once present until deleted here or TTL-expired.)
    """
    stored = await redis.get(TOKEN_KEY)
    if not stored:
        return False
    stored_str = stored.decode() if isinstance(stored, bytes) else stored
    candidate_hash = hashlib.sha256(candidate.encode()).hexdigest()
    if not hmac.compare_digest(stored_str, candidate_hash):
        return False
    deleted = await redis.delete(TOKEN_KEY)
    return bool(deleted == 1)
