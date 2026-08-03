"""Shared FastAPI dependencies for database sessions and request context."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.config import Settings, get_settings
from api.db import get_session_factory

logger = logging.getLogger(__name__)

# Bound any single runaway query (e.g. a pathological GROUP BY from the
# reporting engine) so one request can't hold a connection indefinitely.
# Generous for OLTP + reporting; kills only genuinely stuck statements.
_STATEMENT_TIMEOUT = "30s"

# Give up on a *row lock* far sooner than on a slow query. These are different
# failures: a slow query is doing work, a lock waiter is doing nothing while
# holding a pool connection hostage. Uncontended OLTP lock waits are sub-100ms,
# so 5s only ever fires on real contention — and then it fails fast with a
# legible lock error instead of burning the full statement timeout.
#
# This is defense-in-depth, not the fix. It caps the blast radius of a lock bug:
# with pool_size=10/max_overflow=5, requests stuck for 30s starved the entire API
# (unrelated endpoints ran 37-97s). Matches schema_manager._LOCK_TIMEOUT.
_LOCK_TIMEOUT = "5s"

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

    Unlike get_tenant_db, this session opts into the cross-org RLS bypass
    (``SET LOCAL app.bypass='on'`` → the permissive ``admin_bypass_all`` policy).
    That is intentional: cross-org reads/writes of RLS-forced tables
    (e.g. user_org_memberships in require_org_access, site-admin, entity/workflow
    authoring, import) must see every org. Without the GUC these would fail
    closed to empty results. Tenant-scoped requests go through get_tenant_db
    instead, which drops to app_user with the bypass OFF so RLS is enforced.
    The app connects as the non-superuser km_app role; see api/db_scope.py and
    docs/DATABASE.md.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await db_scope.enter_bypass(session)
            await _apply_timeouts(session)
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


async def _apply_timeouts(session: AsyncSession) -> None:
    """Bound how long this transaction may block, before it runs anything.

    Applied at session setup so the very first statement is already guarded.
    Both are ``SET LOCAL`` (transaction-scoped), so a unit that legitimately
    needs longer — a config promotion, DDL in schema_manager — can raise its own
    afterwards and the pooled connection still resets on commit/rollback.
    """
    await session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    await session.execute(text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))


@asynccontextmanager
async def auth_provisioning_session(settings: Settings) -> AsyncGenerator[AsyncSession]:
    """A short-lived bypass session for auth-time user provisioning.

    Provisioning must **not** run on the request-scoped ``get_db`` session. That
    session commits only when the response is ready, so any row lock it takes on
    ``user_profiles`` is held for the entire request — and a request that then
    inserts a row referencing that user (chat session, document) does so on a
    *second* pooled connection, which blocks on the FK's ``FOR KEY SHARE``
    against its own sibling's ``FOR UPDATE``. Postgres' deadlock detector cannot
    break it: the holder is idle, not waiting. It only ended at the 30s
    statement timeout, having pinned two pool slots the whole time.

    Committing here bounds the lock to the provisioning statement itself.
    Sessions use ``expire_on_commit=False``, so the returned profile's attributes
    stay readable after this block closes.

    Note this also makes provisioning durable independently of the request: a
    first-login user stays provisioned even if the rest of the request fails,
    which is the behaviour we want.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await db_scope.enter_bypass(session)
            await _apply_timeouts(session)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
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
            # Drop to app_user + pin the tenant GUC (bypass OFF) so RLS enforces
            # for everything that follows. SET LOCAL is transaction-scoped and
            # auto-resets on commit/rollback, keeping pooled connections clean.
            await db_scope.enter_tenant(session, org_id)
            # Pin the session timezone so date_trunc() bucket boundaries in the
            # reporting engine are deterministic and reproducible across pooled
            # connections (created_at/updated_at are UTC). SET LOCAL is txn-scoped.
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            await _apply_timeouts(session)
            yield session
            await session.commit()
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            logger.exception("Unhandled exception in tenant DB session (org=%s)", org_id)
            raise
