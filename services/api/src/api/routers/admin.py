"""Global site-admin console endpoints: user management and system status.

Every route requires site-admin privileges. Org CRUD lives in routers/orgs.py
(already site-admin gated); org-scoped membership editing stays in
routers/memberships.py — site admins reach any org there via X-Org-ID.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import CurrentUser, require_site_admin
from api.config import Settings, get_settings
from api.dependencies import get_db, get_redis
from api.repositories.user import UserRepository
from api.schemas.admin import (
    AdminUserUpdate,
    BeatScheduleEntry,
    BeatStatus,
    CeleryActiveTask,
    CeleryQueueItem,
    CeleryStatusRead,
    ComponentStatus,
    JobCancelResult,
    JobLogEntry,
    JobLogsRead,
    SystemStatusRead,
    UserMembershipSummary,
)
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.user import UserRead
from api.services.brain_client import BrainAPIClient
from api.tasks.celery_app import celery_app

router = APIRouter()

# Celery's default queue is a Redis list named after the queue ("celery").
_CELERY_QUEUE_NAME = "celery"
# Keys the worker's beat_heartbeat task writes (see worker/tasks/monitoring.py).
_BEAT_HEARTBEAT_KEY = "celery:beat:heartbeat"
_BEAT_SCHEDULE_KEY = "celery:beat:schedule"
# Per-job log list the worker appends to (see worker/tasks/_job.py).
_JOB_LOG_KEY = "job:logs:{document_id}"
# Cancel flag the worker checks each stage (keyed by task id; see _job.py).
_INGEST_CANCEL_KEY = "ingest:cancel:{task_id}"
_CANCEL_FLAG_TTL_SECONDS = 3600
# Bound how many queue messages we deserialize for the console peek.
_QUEUE_PEEK_LIMIT = 50
# How long to wait for workers to reply to an inspect() broadcast.
_INSPECT_TIMEOUT_SECONDS = 1.0
_CANCELLABLE_STATUSES = ("PENDING", "PROCESSING")


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


def _parse_celery_message(raw: str) -> CeleryQueueItem:
    """Pull the human-relevant fields out of a Celery task envelope (protocol v2).

    A malformed message must still appear in the list (so the operator sees the
    queue isn't empty) rather than break the whole response.
    """
    try:
        headers = (json.loads(raw).get("headers") or {}) if raw else {}
    except (ValueError, TypeError):
        return CeleryQueueItem()
    return CeleryQueueItem(
        task=headers.get("task"),
        id=headers.get("id"),
        eta=headers.get("eta"),
        args=headers.get("argsrepr"),
        kwargs=headers.get("kwargsrepr"),
    )


async def _beat_status(redis: Redis) -> tuple[BeatStatus, list[BeatScheduleEntry]]:
    """Read the beat heartbeat + published schedule the worker stamps into Redis."""
    try:
        raw = await redis.get(_BEAT_HEARTBEAT_KEY)
        raw_schedule = await redis.get(_BEAT_SCHEDULE_KEY)
    except Exception as e:  # noqa: BLE001 — status endpoint must report, not raise
        return BeatStatus(status="down", detail=str(e)), []

    schedule: list[BeatScheduleEntry] = []
    if raw_schedule:
        try:
            schedule = [BeatScheduleEntry(**entry) for entry in json.loads(raw_schedule)]
        except (ValueError, TypeError):
            schedule = []

    if not raw:
        return BeatStatus(status="down", detail="no heartbeat — celery beat is not running"), schedule

    try:
        payload = json.loads(raw)
        last = datetime.fromisoformat(payload["ts"])
        interval = float(payload.get("interval", 15))
    except (ValueError, TypeError, KeyError) as e:
        return BeatStatus(status="down", detail=f"unreadable heartbeat: {e}"), schedule

    age = (datetime.now(UTC) - last).total_seconds()
    # Allow a few missed ticks before calling it stale (clock skew, GC pauses).
    threshold = max(60.0, interval * 3)
    ok = age <= threshold
    return (
        BeatStatus(
            status="ok" if ok else "stale",
            last_tick=payload["ts"],
            age_seconds=round(age, 1),
            detail=None if ok else f"last tick {round(age)}s ago (expected every {round(interval)}s)",
        ),
        schedule,
    )


def _active_document_id(entry: dict[str, Any]) -> str | None:
    """Best-effort extraction of the ingest payload's document_id.

    ``inspect().active()`` reports ``args`` either as the real list (older
    Celery) or a repr string (newer). Only the list form is machine-readable;
    a string arg yields None (the console just won't offer a logs deep-link).
    """
    args = entry.get("args")
    if isinstance(args, (list, tuple)) and args and isinstance(args[0], dict):
        doc_id = args[0].get("document_id")
        return str(doc_id) if doc_id else None
    return None


def _inspect_active_tasks() -> list[CeleryActiveTask]:
    """Tasks currently executing across all workers, via a control broadcast.

    Blocking (waits up to the inspect timeout for replies), so the caller runs
    this in a threadpool. Returns [] if no worker replies or the broker is down.
    """
    try:
        inspector = celery_app.control.inspect(timeout=_INSPECT_TIMEOUT_SECONDS)
        active = inspector.active() or {}
    except Exception:  # noqa: BLE001 — inspection failure must not 500 the console
        return []
    tasks: list[CeleryActiveTask] = []
    for worker_name, entries in active.items():
        for entry in entries or []:
            args = entry.get("args")
            kwargs = entry.get("kwargs")
            tasks.append(
                CeleryActiveTask(
                    task=entry.get("name"),
                    id=entry.get("id"),
                    worker=worker_name,
                    args=str(args) if args else None,
                    kwargs=str(kwargs) if kwargs else None,
                    document_id=_active_document_id(entry),
                )
            )
    return tasks


async def _attach_ingest_progress(session: AsyncSession, active: list[CeleryActiveTask]) -> None:
    """Join each active ingest task to its document's status + coarse progress.

    Cross-org lookup on the privileged console session (bypasses RLS by design —
    this is the global operator view). Tasks whose document_id didn't resolve are
    left as-is (no progress shown).
    """
    ids = [t.document_id for t in active if t.document_id]
    if not ids:
        return
    rows = (
        await session.execute(
            text("SELECT id, processing_status, processing_details FROM documents WHERE id::text = ANY(:ids)"),
            {"ids": ids},
        )
    ).all()
    by_id = {str(row[0]): (row[1], row[2] or {}) for row in rows}
    for task in active:
        record = by_id.get(task.document_id or "")
        if record is None:
            continue
        status_value, details = record
        task.status = status_value
        percent = details.get("percent")
        task.percent = int(percent) if isinstance(percent, (int, float)) else None
        task.stage = details.get("stage")


@router.get("/celery", response_model=CeleryStatusRead)
async def celery_status(
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> CeleryStatusRead:
    """Celery beat liveness + schedule, queued (pending) messages, and running tasks.

    Backs the site-admin console's Celery tab: a `down` beat with a non-draining
    queue is the operator's cue that scheduled work (workflow outbox sweeps,
    document ingest) has stalled. ``active`` surfaces what's executing right now
    (e.g. an in-flight ingest) with its coarse progress, rather than only what's
    waiting.
    """
    try:
        depth = int(await redis.llen(_CELERY_QUEUE_NAME))  # type: ignore[arg-type]  # sync/async union
        raw_items = list(await redis.lrange(_CELERY_QUEUE_NAME, 0, _QUEUE_PEEK_LIMIT - 1))  # type: ignore[misc]
    except Exception:  # noqa: BLE001 — a broker hiccup must not 500 the console
        depth, raw_items = 0, []
    items = [_parse_celery_message(r) for r in raw_items]
    beat, schedule = await _beat_status(redis)
    active = await run_in_threadpool(_inspect_active_tasks)
    await _attach_ingest_progress(session, active)
    return CeleryStatusRead(
        queue_name=_CELERY_QUEUE_NAME,
        depth=depth,
        items=items,
        truncated=depth > len(items),
        beat=beat,
        schedule=schedule,
        active=active,
    )


@router.post("/jobs/{document_id}/cancel", response_model=JobCancelResult)
async def cancel_job(
    document_id: uuid.UUID,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobCancelResult:
    """Cancel a document's in-progress ingest from the global console (any org).

    The tenant-scoped ``/documents/{id}/cancel`` requires org membership; a site
    admin operating the console may not be in the document's org, so this is the
    cross-org equivalent (privileged session, no RLS). Same mechanics: set the
    Redis cancel flag the worker checks, revoke+terminate the task, mark
    CANCELLED, and purge any partial vectors.
    """
    row = (
        await session.execute(
            text(
                "SELECT org_id, document_key, celery_task_id, processing_status "
                "FROM documents WHERE id = :id"
            ),
            {"id": document_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    org_id, document_key, task_id, proc_status = row
    if proc_status not in _CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document is not being processed (status: {proc_status})",
        )

    if task_id:
        # Best-effort: the flag + revoke are belt-and-suspenders, and the DB flip
        # below is the source of truth, so a broker hiccup must not block cancel.
        with contextlib.suppress(Exception):
            await redis.set(_INGEST_CANCEL_KEY.format(task_id=task_id), "1", ex=_CANCEL_FLAG_TTL_SECONDS)
        with contextlib.suppress(Exception):
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    await session.execute(
        text(
            "UPDATE documents SET processing_status = 'CANCELLED', "
            "processing_details = '{\"stage\": \"cancelled\"}'::jsonb WHERE id = :id"
        ),
        {"id": document_id},
    )
    await session.commit()

    # Purge any partial vectors/claims written before the abort (best-effort).
    with contextlib.suppress(Exception):
        await BrainAPIClient(settings).remove_document(str(org_id), document_key)

    return JobCancelResult(document_id=str(document_id), status="CANCELLED")


@router.get("/jobs/{document_id}/logs", response_model=JobLogsRead)
async def job_logs(
    document_id: str,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> JobLogsRead:
    """Ingest job log lines for a document (site-admin console drill-in).

    Reads the same capped Redis list the worker appends to. Cross-org by design
    — this is the global console; per-org access to a document's own logs is the
    tenant-scoped ``/documents/{id}/logs`` endpoint.
    """
    try:
        raw = list(await redis.lrange(_JOB_LOG_KEY.format(document_id=document_id), 0, -1))  # type: ignore[misc]
    except Exception:  # noqa: BLE001 — a broker hiccup must not 500 the console
        raw = []
    events: list[JobLogEntry] = []
    for item in raw:
        try:
            events.append(JobLogEntry(**json.loads(item)))
        except (ValueError, TypeError):
            continue
    return JobLogsRead(document_id=document_id, events=events)
