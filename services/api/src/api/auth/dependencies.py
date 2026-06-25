"""FastAPI auth dependencies: get_current_user, require_org_access, require_site_admin."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth.clerk import validate_clerk_token
from api.auth.keycloak import validate_keycloak_token
from api.config import Settings, get_settings
from api.dependencies import get_db, get_org_id
from api.models.user import UserOrgMembership
from api.services.user_provisioning import provision_user_from_claims

logger = logging.getLogger(__name__)

# `auto_error=False` makes the scheme optional so the e2e test path can
# skip the Authorization header entirely.
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated user extracted from the JWT and database."""

    sub: str
    username: str
    email: str
    profile_id: uuid.UUID
    is_site_admin: bool


@dataclass(frozen=True, slots=True)
class OrgContext:
    """The user's membership context within a specific org."""

    user: CurrentUser
    org_id: uuid.UUID
    membership: UserOrgMembership
    is_org_admin: bool


async def _resolve_e2e_user(
    test_user: str,
    test_secret: str,
    settings: Settings,
    session: AsyncSession,
) -> CurrentUser:
    """Resolve a user via the E2E test bypass.

    Accepts any sub/username in X-Test-User as long as the shared secret matches.
    The UserProfile is auto-provisioned on first use.
    """
    expected = settings.e2e_test_secret.get_secret_value()
    if not expected or test_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid E2E test credentials",
        )

    # Parse X-Test-User as "username:email" for convenience; default to
    # synthetic email when only the username is provided.
    username, _, email = test_user.partition(":")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Test-User must be provided",
        )
    email = email or f"{username}@e2e.local"

    profile = await provision_user_from_claims(
        session, sub=f"e2e-{username}", username=username, email=email
    )
    return CurrentUser(
        sub=profile.keycloak_sub,
        username=profile.username,
        email=profile.email,
        profile_id=profile.id,
        is_site_admin=profile.is_site_admin,
    )


def _token_issuer(token: str) -> str:
    """Read the `iss` claim WITHOUT verifying the signature, only to route to a
    verifier. The verified decode re-pins the issuer independently, so a forged
    `iss` cannot bypass signature/issuer validation (mirrors the Go verifier)."""
    try:
        claims = jose_jwt.get_unverified_claims(token)
    except JWTError:
        return ""
    issuer = claims.get("iss", "")
    return issuer if isinstance(issuer, str) else ""


async def _verify_bearer_token(token: str, settings: Settings) -> dict[str, Any]:
    """Dual-verify a bearer token by issuer (Keycloak or Clerk — D4 coexistence).

    A token is routed to exactly one verifier by its `iss`; each verifier pins
    its own issuer + JWKS + provider-specific check (Keycloak `aud`, Clerk `azp`).
    """
    issuer = _token_issuer(token)
    clerk_issuer = (
        settings.clerk_jwt_issuer.rstrip("/") if settings.clerk_jwt_issuer else ""
    )
    keycloak_issuer = (
        f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
        if settings.keycloak_url
        else ""
    )

    if clerk_issuer and issuer == clerk_issuer:
        return await validate_clerk_token(
            token, issuer=clerk_issuer, allowed_azp=settings.clerk_allowed_azp_list
        )
    if keycloak_issuer and issuer == keycloak_issuer:
        return await validate_keycloak_token(
            token,
            keycloak_url=settings.keycloak_url,
            realm=settings.keycloak_realm,
            client_id=settings.keycloak_client_id,
        )
    raise JWTError("Token issuer matches no configured auth provider")


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
    x_test_user: Annotated[str | None, Header(alias="X-Test-User")] = None,
    x_test_secret: Annotated[str | None, Header(alias="X-Test-Secret")] = None,
) -> CurrentUser:
    """Validate the bearer token (or E2E bypass) and return the current user."""
    # E2E test mode takes precedence, but is locked behind the config flag
    # AND a matching shared secret. Never active in production.
    if settings.e2e_test_mode and x_test_user:
        return await _resolve_e2e_user(
            x_test_user, x_test_secret or "", settings, session
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required",
        )

    try:
        claims = await _verify_bearer_token(credentials.credentials, settings)
    except Exception as e:
        logger.warning("Token validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e

    sub = claims.get("sub", "")
    # Clerk exposes `username` (via JWT template); Keycloak exposes
    # `preferred_username`. Either populates the username (mirrors the Go path).
    username = claims.get("username") or claims.get("preferred_username") or ""
    email = claims.get("email", "")

    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    profile = await provision_user_from_claims(
        session, sub=sub, username=username, email=email
    )

    return CurrentUser(
        sub=sub,
        username=profile.username,
        email=profile.email,
        profile_id=profile.id,
        is_site_admin=profile.is_site_admin,
    )


async def require_org_access(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    org_id: Annotated[uuid.UUID, Depends(get_org_id)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OrgContext:
    """Require the current user to have a membership in the requested org.

    Site admins get full access (synthetic membership).
    """
    result = await session.execute(
        select(UserOrgMembership)
        .where(
            UserOrgMembership.profile_id == user.profile_id,
            UserOrgMembership.org_id == org_id,
        )
        .options(
            selectinload(UserOrgMembership.regions),
            selectinload(UserOrgMembership.departments),
            selectinload(UserOrgMembership.roles),
            selectinload(UserOrgMembership.groups),
        )
    )
    membership = result.scalar_one_or_none()

    if membership is None and not user.is_site_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No membership in the requested organization",
        )

    # Site admins without explicit membership get elevated access
    if membership is None:
        membership = UserOrgMembership(
            profile_id=user.profile_id,
            org_id=org_id,
            is_org_admin=True,
        )

    return OrgContext(
        user=user,
        org_id=org_id,
        membership=membership,
        is_org_admin=membership.is_org_admin or user.is_site_admin,
    )


async def require_org_admin(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
) -> OrgContext:
    """Require org-admin privileges within the current org."""
    if not ctx.is_org_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin access required",
        )
    return ctx


async def require_site_admin(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """Require the current user to be a site admin."""
    if not user.is_site_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Site admin access required",
        )
    return user


async def require_internal_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
) -> None:
    """Authenticate internal service-to-service calls (e.g. worker callbacks).

    Uses a shared secret distinct from brain_api_key so compromise of one
    service credential does not grant access to both surfaces. Configured
    secrets must be non-empty — an empty configured key disables the
    endpoint rather than allowing anonymous access.
    """
    expected = settings.internal_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API disabled (no key configured)",
        )
    if not x_internal_api_key or x_internal_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API credentials",
        )
