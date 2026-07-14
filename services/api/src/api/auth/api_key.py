"""API-key authentication for the public ``/api/v1`` surface.

External/enterprise callers cannot present a Clerk browser JWT, so this module
provides the programmatic auth path:

1. :func:`require_api_key` reads the key from ``Authorization: Bearer km2_...`` (or
   the ``X-API-Key`` header), hashes it, and resolves it to an :class:`ApiKeyPrincipal`
   by looking up the hash on a **short-lived privileged** session — cross-tenant,
   before any org is known, exactly like the inbound-webhook token path. The
   session commits (the debounced ``last_used_at`` touch) and closes *before* the
   endpoint runs, so it never holds a row lock across a slow request.
2. :func:`require_scope` gates an endpoint on a single concrete scope.
3. :func:`get_apikey_tenant_db` opens an RLS session scoped to the key's org
   (mirrors :func:`api.dependencies.get_tenant_db`, but the org comes from the key
   rather than the ``X-Org-ID`` header).
4. :func:`enforce_ip_rate_limit` throttles by client IP *before* key resolution,
   and :func:`enforce_api_rate_limit` applies the per-key Redis quota.

Every failure returns an opaque ``401`` so a caller learns nothing about which
check tripped (unknown key vs revoked vs expired).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.dependencies import get_redis
from api.models.api_key import ApiKey
from api.repositories.api_key import lookup_by_key_hash
from api.services.api_key_scopes import has_scope
from api.services.api_key_service import hash_key, is_expired
from api.services.api_rate_limit import check_rate_limit

logger = logging.getLogger(__name__)

_KEY_SCHEME_PREFIX = "km2_"
# Debounce last_used_at writes so a hot key doesn't cause a DB write per request.
_LAST_USED_DEBOUNCE_SECONDS = 60

# auto_error=False: the scheme is optional here so we can also accept the key via
# the X-API-Key header and return one consistent 401 when neither is present.
_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API key",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True, slots=True)
class ApiKeyPrincipal:
    """The resolved caller behind a valid API key."""

    api_key_id: uuid.UUID
    org_id: uuid.UUID
    scopes: frozenset[str]
    name: str


def _presented_key(
    credentials: HTTPAuthorizationCredentials | None,
    x_api_key: str | None,
) -> str | None:
    """Pull the ``km2_...`` secret from the bearer header or ``X-API-Key``."""
    if credentials is not None and credentials.credentials.startswith(_KEY_SCHEME_PREFIX):
        return credentials.credentials
    if x_api_key and x_api_key.startswith(_KEY_SCHEME_PREFIX):
        return x_api_key
    return None


async def _touch_last_used(session: AsyncSession, api_key: ApiKey) -> None:
    """Best-effort, debounced update of ``last_used_at`` (skips hot-key churn)."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=_LAST_USED_DEBOUNCE_SECONDS)
    await session.execute(
        update(ApiKey)
        .where(ApiKey.id == api_key.id)
        .where(or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < cutoff))
        .values(last_used_at=now)
    )


async def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ApiKeyPrincipal:
    """Resolve the presented API key to a principal, or raise an opaque 401.

    The hash lookup + ``last_used_at`` touch run on a dedicated short-lived
    privileged session that commits and closes here — NOT on the request's data
    session. This keeps the touch off the request transaction so (a) the key's row
    lock is never held across a slow request (which would serialise every other
    call using the same key), and (b) ``last_used_at`` is recorded even when the
    request itself later errors (403/404/429). The endpoint's own data work runs
    on a separate RLS session (:func:`get_apikey_tenant_db`).
    """
    presented = _presented_key(credentials, x_api_key)
    if presented is None:
        raise _UNAUTHORIZED

    factory = get_session_factory(settings)
    async with factory() as session:
        # Resolve the key by hash across every org (the org is unknown until we
        # find the row); api_keys is RLS-forced, so this needs the bypass. The
        # endpoint's own data work runs separately on get_apikey_tenant_db.
        await db_scope.enter_bypass(session)
        api_key = await lookup_by_key_hash(session, hash_key(presented))
        if api_key is None or api_key.revoked_at is not None or is_expired(api_key):
            raise _UNAUTHORIZED
        principal = ApiKeyPrincipal(
            api_key_id=api_key.id,
            org_id=api_key.org_id,
            scopes=frozenset(api_key.scopes or ()),
            name=api_key.name,
        )
        await _touch_last_used(session, api_key)
        await session.commit()
    return principal


def require_scope(scope: str) -> Callable[..., Awaitable[ApiKeyPrincipal]]:
    """Dependency factory: require ``scope`` on the calling key (else 403)."""

    async def _dependency(
        principal: Annotated[ApiKeyPrincipal, Depends(require_api_key)],
    ) -> ApiKeyPrincipal:
        if not has_scope(principal.scopes, scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key is missing the required scope: {scope}",
            )
        return principal

    return _dependency


async def enforce_ip_rate_limit(
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Coarse per-client-IP throttle, applied BEFORE key resolution.

    This runs ahead of :func:`require_api_key` (it takes no principal), so a flood
    of missing/invalid/unknown keys can't hammer the auth DB lookup without bound —
    the per-key limiter below only engages once a *valid* key is resolved. Keyed by
    the direct socket peer; behind a reverse proxy this is the proxy address, so
    tighten with real client-IP extraction if deployed that way. Best-effort
    (fail-open) like the per-key limiter.
    """
    client_ip = request.client.host if request.client else "unknown"
    result = await check_rate_limit(redis, f"ip:{client_ip}", limit=settings.api_ip_rate_limit_per_minute)
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(result.retry_after)},
        )


async def enforce_api_rate_limit(
    response: Response,
    principal: Annotated[ApiKeyPrincipal, Depends(require_api_key)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Per-key Redis quota. Sets ``X-RateLimit-*`` headers; 429 when exhausted."""
    result = await check_rate_limit(
        redis, f"apikey:{principal.api_key_id}", limit=settings.api_rate_limit_per_minute
    )
    rate_headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
    }
    response.headers.update(rate_headers)
    if not result.allowed:
        # Carry the rate-limit headers on the 429 too — FastAPI builds the error
        # response from the exception and would otherwise drop the ones set on
        # `response` above.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={**rate_headers, "Retry-After": str(result.retry_after)},
        )


async def get_apikey_tenant_db(
    principal: Annotated[ApiKeyPrincipal, Depends(require_api_key)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncSession]:
    """RLS session scoped to the API key's org (mirrors ``get_tenant_db``)."""
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await db_scope.enter_tenant(session, principal.org_id)
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            await session.execute(text("SET LOCAL statement_timeout = '30s'"))
            yield session
            await session.commit()
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            logger.exception("Unhandled exception in API-key tenant session (org=%s)", principal.org_id)
            raise


async def get_apikey_tenant_owner_db(
    principal: Annotated[ApiKeyPrincipal, Depends(require_api_key)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncSession]:
    """RLS session scoped to the key's org, but on the DDL-capable ``km_app`` role.

    A config promotion applies entity changes, which run ``ce_*`` table DDL — so it
    needs :func:`db_scope.enter_tenant_owner` (keeps CREATE) rather than the
    least-privilege ``app_user`` scope of :func:`get_apikey_tenant_db`. RLS still
    enforces (km_app is non-superuser), and the statement timeout is relaxed because
    a large promotion legitimately runs longer than a data-plane call. Reserve this
    for the config-write surface only; data-plane routes keep the least-privilege
    session.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await db_scope.enter_tenant_owner(session, principal.org_id)
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            await session.execute(text("SET LOCAL statement_timeout = '300s'"))
            yield session
            await session.commit()
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            logger.exception("Unhandled exception in API-key owner session (org=%s)", principal.org_id)
            raise
