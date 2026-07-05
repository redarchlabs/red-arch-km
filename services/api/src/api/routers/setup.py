"""First-run setup endpoints: bootstrap the first site admin.

`GET /status` is intentionally unauthenticated — the UI needs it before login
to decide whether to show the wizard, and it reveals only whether the instance
is initialized. `POST /claim` requires a signed-in Clerk user plus the
one-time token from the API server logs.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import CurrentUser, get_current_user
from api.config import Settings, get_settings
from api.dependencies import get_db, get_redis
from api.repositories.user import UserRepository
from api.schemas.setup import SetupClaimRequest, SetupClaimResponse, SetupStatusRead
from api.services.setup_token import TOKEN_KEY, consume_setup_token, site_admin_exists

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status", response_model=SetupStatusRead)
async def setup_status(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SetupStatusRead:
    return SetupStatusRead(needs_setup=not await site_admin_exists(session))


@router.post("/claim", response_model=SetupClaimResponse)
async def claim_setup(
    body: SetupClaimRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SetupClaimResponse:
    """Promote the calling user to site admin in exchange for the setup token."""
    if await site_admin_exists(session):
        # Instance already initialized — clean up any lingering token so it
        # can never be replayed later.
        await redis.delete(TOKEN_KEY)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already completed",
        )

    if not await consume_setup_token(redis, body.token):
        # Distinct audit line: repeated hits here are the signal of an
        # attempted takeover (the token has 256 bits of entropy, so guessing
        # is hopeless — but the attempts should be visible).
        logger.warning(
            "Failed setup-claim attempt by %s (%s): invalid or already-used token",
            user.username,
            user.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or already-used setup token",
        )

    # The token is already consumed; commit the promotion HERE so a later
    # failure can restore the token instead of burning it with no admin
    # created. (Worst case — crash between consume and restore — self-heals:
    # the next boot re-mints a token because no active site admin exists.)
    try:
        repo = UserRepository(session)
        profile = await repo.get(user.profile_id)
        if profile is None:
            msg = "Profile disappeared after provisioning"
            raise RuntimeError(msg)
        profile.is_site_admin = True
        await session.commit()
    except Exception:
        token_hash = hashlib.sha256(body.token.encode()).hexdigest()
        try:
            await redis.set(TOKEN_KEY, token_hash, ex=settings.setup_token_ttl_seconds)
        except Exception:
            logger.exception("Could not restore setup token after failed claim")
        raise

    logger.warning("First-run setup claimed: %s (%s) is now site admin", user.username, user.email)
    return SetupClaimResponse(claimed=True)
