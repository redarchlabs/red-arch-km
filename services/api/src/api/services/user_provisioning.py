"""Auto-provision UserProfile records on first Clerk login."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import UserProfile

logger = logging.getLogger(__name__)


async def provision_user_from_claims(
    session: AsyncSession,
    *,
    sub: str,
    username: str,
    email: str,
) -> UserProfile:
    """Find or create a UserProfile for the given Clerk subject.

    Called on every authenticated request; the first call creates the record.
    The ``keycloak_sub`` column now stores the Clerk subject (the column rename
    to ``auth_subject`` is tracked separately as a deferred DB migration).
    """
    result = await session.execute(select(UserProfile).where(UserProfile.keycloak_sub == sub))
    profile = result.scalar_one_or_none()

    if profile is not None:
        # Keep email/username in sync with the IdP claims
        changed = False
        if profile.username != username:
            profile.username = username
            changed = True
        if profile.email != email:
            profile.email = email
            changed = True
        if changed:
            await session.flush()
        return profile

    profile = UserProfile(
        keycloak_sub=sub,
        username=username,
        email=email,
        is_site_admin=False,
    )
    session.add(profile)
    await session.flush()
    logger.info("Provisioned new UserProfile for %s (%s)", username, sub)
    return profile
