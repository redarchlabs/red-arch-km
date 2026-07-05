"""Global site-admin console endpoints: user management and system status.

Every route requires site-admin privileges. Org CRUD lives in routers/orgs.py
(already site-admin gated); org-scoped membership editing stays in
routers/memberships.py — site admins reach any org there via X-Org-ID.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import CurrentUser, require_site_admin
from api.config import Settings, get_settings
from api.dependencies import get_db, get_redis
from api.repositories.user import UserRepository
from api.schemas.admin import (
    AdminUserUpdate,
    ComponentStatus,
    SystemStatusRead,
    UserMembershipSummary,
)
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.user import UserRead
from api.services.brain_client import BrainAPIClient

router = APIRouter()

# Celery's default queue is a Redis list named after the queue ("celery").
_CELERY_QUEUE_NAME = "celery"


@router.get("/users", response_model=PaginatedResponse[UserRead])
async def list_all_users(
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
    q: Annotated[str | None, Query(max_length=200, description="Filter by username/email substring")] = None,
) -> PaginatedResponse[UserRead]:
    """List all users across the instance (site-admin console)."""
    repo = UserRepository(session)
    users, total = await repo.list_all(offset=pagination.offset, limit=pagination.page_size, q=q)
    return make_page([UserRead.model_validate(u) for u in users], total, pagination)


@router.patch("/users/{profile_id}", response_model=UserRead)
async def update_user_flags(
    profile_id: uuid.UUID,
    body: AdminUserUpdate,
    admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserRead:
    """Promote/demote site admins and activate/deactivate accounts."""
    repo = UserRepository(session)
    profile = await repo.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    demoting = body.is_site_admin is False and profile.is_site_admin
    deactivating = body.is_active is False and profile.is_active

    if profile.id == admin.profile_id and (demoting or deactivating):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot demote or deactivate your own account",
        )

    # Never let the instance lose its last active site admin. A transaction-
    # scoped advisory lock serializes concurrent demote/deactivate requests —
    # without it, two PATCHes each removing the *other* of the last two
    # admins could both pass the count check.
    target_is_active_admin = profile.is_site_admin and profile.is_active
    if (demoting or deactivating) and target_is_active_admin:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('site_admin_flags'))"))
        if await repo.count_active_site_admins() <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the last active site admin",
            )

    if body.is_site_admin is not None:
        profile.is_site_admin = body.is_site_admin
    if body.is_active is not None:
        profile.is_active = body.is_active
    await session.flush()
    return UserRead.model_validate(profile)


@router.get("/users/{profile_id}/memberships", response_model=list[UserMembershipSummary])
async def list_user_memberships(
    profile_id: uuid.UUID,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserMembershipSummary]:
    """All org memberships of one user, across every org (user-centric view)."""
    repo = UserRepository(session)
    if await repo.get(profile_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    rows = await repo.list_memberships_with_orgs(profile_id)
    return [
        UserMembershipSummary(
            membership_id=membership.id,
            org_id=org.id,
            org_name=org.name,
            is_org_admin=membership.is_org_admin,
        )
        for membership, org in rows
    ]


async def _probe_db(session: AsyncSession) -> ComponentStatus:
    start = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001 — status endpoint must report, not raise
        return ComponentStatus(status="error", detail=str(e))
    return ComponentStatus(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


async def _probe_redis(redis: Redis) -> ComponentStatus:
    start = time.perf_counter()
    try:
        await redis.ping()
    except Exception as e:  # noqa: BLE001
        return ComponentStatus(status="error", detail=str(e))
    return ComponentStatus(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


async def _probe_brain_api(settings: Settings) -> ComponentStatus:
    start = time.perf_counter()
    try:
        await BrainAPIClient(settings).healthz()
    except Exception as e:  # noqa: BLE001
        return ComponentStatus(status="error", detail=str(e))
    return ComponentStatus(status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2))


async def _probe_celery_queue(redis: Redis) -> ComponentStatus:
    """Queue depth of the default Celery list — a growing number with no
    worker consuming it is the operator's cue that document processing is down."""
    try:
        # redis-py types llen as `Awaitable[int] | int` (sync/async union).
        depth = await redis.llen(_CELERY_QUEUE_NAME)  # type: ignore[misc]
    except Exception as e:  # noqa: BLE001
        return ComponentStatus(status="error", detail=str(e))
    return ComponentStatus(status="ok", detail=f"depth={depth}")


@router.get("/system", response_model=SystemStatusRead)
async def system_status(
    request: Request,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SystemStatusRead:
    """Health of the platform's moving parts, for the console's System tab."""
    return SystemStatusRead(
        version=request.app.version,
        components={
            "database": await _probe_db(session),
            "redis": await _probe_redis(redis),
            "brain_api": await _probe_brain_api(settings),
            "worker_queue": await _probe_celery_queue(redis),
        },
    )
