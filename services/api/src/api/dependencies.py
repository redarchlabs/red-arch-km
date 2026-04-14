"""Shared FastAPI dependencies for database sessions and request context."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.db import get_session_factory


async def get_db(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncGenerator[AsyncSession]:
    """Provide a plain async database session (no tenant context).

    Use this for endpoints that don't need org scoping (e.g. /api/auth/me,
    /healthz, site-admin operations that span orgs).
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
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

    Sets `app.current_tenant_id` on the session so PostgreSQL RLS policies
    automatically scope all queries to the current org.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            # set_config(name, value, is_local=true) supports bind parameters
            # where SET LOCAL does not. The transaction-local scope ensures
            # the setting is cleared when the session's transaction ends.
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(org_id)},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
