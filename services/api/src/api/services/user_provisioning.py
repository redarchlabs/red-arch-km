"""Auto-provision UserProfile records on first Clerk login."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.user import UserProfile

logger = logging.getLogger(__name__)


def _should_be_site_admin(settings: Settings | None, email: str, *, asserted: bool) -> bool:
    """Is this address pre-authorized as a site admin?

    ``asserted`` gates the whole check: only an email the IdP actually put in the
    token counts. The sub-derived ``…@placeholder.invalid`` fallback is not an
    identity claim, and matching on it would let the allow-list be satisfied by a
    value this code made up.

    ``settings`` is passed in rather than fetched from the global cache so this
    stays a pure function of its inputs — the caller (auth) already holds it.
    """
    if settings is None or not asserted or not email:
        return False
    return email.strip().lower() in settings.site_admin_emails


async def provision_user_from_claims(
    session: AsyncSession,
    *,
    sub: str,
    username: str,
    email: str,
    settings: Settings | None = None,
) -> UserProfile:
    """Find or create a UserProfile for the given Clerk subject.

    Called on every authenticated request; the first call creates the record.
    The ``auth_subject`` column stores the OIDC subject (renamed from
    ``keycloak_sub`` in migration 003, D3/AC-4.1).

    Re-sync of an existing row is deliberately **one-way**: only a claim that is
    actually present may overwrite a stored value. A token without the claim
    leaves the column alone.

    That asymmetry is load-bearing, not defensive tidiness. The same user reaches
    this code with different claim sets — a Clerk JWT-template token carries
    ``email``/``username``, the default session token (what ``getToken()`` returns
    with no template, e.g. from the MCP client) carries neither. Overwriting with
    the sub-derived fallback made the row flip back and forth, so nearly every
    authenticated request emitted an UPDATE. Both columns are UNIQUE-indexed, so
    Postgres escalates that UPDATE from ``FOR NO KEY UPDATE`` to a full
    ``FOR UPDATE`` tuple lock — which blocks the ``FOR KEY SHARE`` lock every
    INSERT referencing the row must take (``chat_sessions.user_id``,
    ``documents.uploaded_by_id``). Those inserts run on a *different* pooled
    connection than this one, so a request blocked on itself and only unwedged
    when ``statement_timeout`` killed it 30s later.
    """
    # Track whether the IdP actually asserted each value *before* applying the
    # fallbacks below — only an asserted claim is allowed to overwrite.
    has_username_claim = bool(username)
    has_email_claim = bool(email)

    # Without a Clerk JWT template the default session token carries no
    # username/email claims. Fall back to sub-derived values — they are unique,
    # so two claimless users can't collide on the empty string, and they are
    # resynced to the real values once a template is configured.
    username = username or sub
    email = email or f"{sub}@placeholder.invalid"

    result = await session.execute(select(UserProfile).where(UserProfile.auth_subject == sub))
    profile = result.scalar_one_or_none()

    promote = _should_be_site_admin(settings, email, asserted=has_email_claim)

    if profile is not None:
        # Keep email/username in sync with the IdP claims — but only from a
        # token that carries them. A claimless token is a pure read.
        changed = False
        if has_username_claim and profile.username != username:
            profile.username = username
            changed = True
        if has_email_claim and profile.email != email:
            profile.email = email
            changed = True
        if promote and not profile.is_site_admin:
            # One-way: the allow-list grants, it never revokes. Dropping an address
            # from it must not silently demote someone mid-session — that is the
            # site-admin console's job, which records who did it.
            profile.is_site_admin = True
            changed = True
            logger.warning("Promoted %s to site admin via SITE_ADMIN_EMAILS", email)
        if changed:
            await session.flush()
        return profile

    profile = UserProfile(
        auth_subject=sub,
        username=username,
        email=email,
        is_site_admin=promote,
    )
    if promote:
        logger.warning("Provisioned %s as a site admin via SITE_ADMIN_EMAILS", email)
    session.add(profile)
    await session.flush()
    logger.info("Provisioned new UserProfile for %s (%s)", username, sub)
    return profile
