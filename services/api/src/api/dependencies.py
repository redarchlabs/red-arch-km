"""Shared FastAPI dependencies for database sessions and request context."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.db import get_session_factory

logger = logging.getLogger(__name__)

_redis_client: Redis | None = None


def get_redis_client(settings: Settings) -> Redis:
    """Process-wide async Redis client (connection pool under the hood)."""
    global _redis_client  # noqa: PLW0603
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_redis_client() -> None:
    """Dispose the shared Redis pool on shutdown (mirrors db.dispose_engine)."""
    global _redis_client  # noqa: PLW0603
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def get_redis(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Redis:
    """FastAPI dependency for the shared Redis client."""
    return get_redis_client(settings)


async def get_db(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncSession]:
    """Provide a plain async database session (no tenant context).

    Use this for endpoints that don't need org scoping (e.g. /api/auth/me,
    /healthz, site-admin operations that span orgs).

    Unlike get_tenant_db, this session stays on the privileged connection role
    (superuser/BYPASSRLS, as in the shipped compose files) and does NOT drop to
    app_user. That is intentional: cross-org reads of RLS-forced tables
    (e.g. user_org_memberships in require_org_access) must bypass RLS. Under a
    restricted role those reads would fail closed to empty results. Tenant-scoped
    requests go through get_tenant_db instead, which drops to app_user so RLS is
    enforced. See docs/DATABASE.md.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except HTTPException:
            # Expected control-flow exception — roll back and let FastAPI
            # translate it into the correct HTTP response. Not logged as
            # an error because it isn't one.
            await session.rollback()
            raise
        except Exception:
            # Unexpected DB/SQLAlchemy/programming error: log before re-raising
            # so the traceback is captured alongside request metadata rather
            # than lost.
            await session.rollback()
            logger.exception("Unhandled exception in DB session")
            raise


async def get_org_id(
    x_org_id: Annotated[str | None, Header(alias="X-Org-ID")] = None,
) -> uuid.UUID:
    """Extract and validate the current org ID from the X-Org-ID header."""
    if not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-ID header is required",
        )
    try:
        return uuid.UUID(x_org_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-ID must be a valid UUID",
        ) from e


async def get_tenant_db(
    org_id: Annotated[uuid.UUID, Depends(get_org_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncSession]:
    """Provide an async session with RLS tenant context set.

    Two things happen inside the session's transaction:

    1. `SET LOCAL ROLE app_user` drops off the privileged connection role
       (superuser/BYPASSRLS) down to the non-superuser, non-BYPASSRLS `app_user`
       role. RLS is bypassed for superusers/BYPASSRLS roles even under FORCE ROW
       LEVEL SECURITY, so without this the tenant policies never enforce. This
       mirrors the `SET ROLE app_user` used by the integration RLS harness
       (tests/integration/conftest.py).
    2. `set_config('app.current_tenant_id', ...)` sets the GUC the RLS policies
       compare `org_id` against, scoping every query to the current org.

    Both use transaction-local scope (`SET LOCAL` / is_local=true) so the pooled
    connection is reset — role restored, GUC cleared — when the transaction ends.
    The role requires migration 007 to have created `app_user` with grants.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            # Drop to app_user first so RLS is enforced for everything that
            # follows. SET LOCAL is transaction-scoped and auto-resets on
            # commit/rollback, keeping pooled connections clean.
            await session.execute(text("SET LOCAL ROLE app_user"))
            # set_config(name, value, is_local=true) supports bind parameters
            # where SET LOCAL does not. The transaction-local scope ensures
            # the setting is cleared when the session's transaction ends.
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(org_id)},
            )
            yield session
            await session.commit()
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            logger.exception("Unhandled exception in tenant DB session (org=%s)", org_id)
            raise
