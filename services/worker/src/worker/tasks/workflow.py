"""Workflow engine worker tasks.

The worker holds no database connection — these tasks are thin ``httpx``
callbacks into the API's internal endpoints, which do the RLS-scoped work.
``sweep_outbox`` runs on the celery-beat cadence to drain pending events;
``maintain_partitions`` pre-creates month partitions.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from worker.celery_app import app
from worker.config import WorkerSettings

logger = logging.getLogger(__name__)


def _post(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    settings = WorkerSettings()
    if not settings.internal_api_key:
        logger.warning("INTERNAL_API_KEY not set — skipping %s", path)
        return None
    try:
        response = httpx.post(
            f"{settings.api_url.rstrip('/')}{path}",
            params=params or {},
            headers={"X-Internal-API-Key": settings.internal_api_key},
            timeout=60,
        )
        response.raise_for_status()
        return response.json() if response.content else None
    except Exception as exc:  # noqa: BLE001 - beat tasks must not crash the worker
        logger.warning("workflow callback %s failed: %s", path, exc)
        return None


@app.task  # type: ignore[untyped-decorator]  # celery's app.task is untyped
def sweep_outbox(limit: int = 200) -> dict[str, Any] | None:
    """Drain a batch of pending workflow-outbox events via the API."""
    return _post("/api/internal/workflows/dispatch-batch", {"limit": limit})


@app.task  # type: ignore[untyped-decorator]
def run_timers() -> dict[str, Any] | None:
    """Resume due delayed runs + fire due scheduled workflows via the API."""
    return _post("/api/internal/workflows/run-timers")


@app.task  # type: ignore[untyped-decorator]
def advance_tokens(limit: int = 200) -> dict[str, Any] | None:
    """Drive the BPMN token engine: reactivate due parked tokens + drain the
    active token queue (resumes timers/retries and any interrupted v2 runs)."""
    return _post("/api/internal/workflows/advance-tokens", {"limit": limit})


@app.task  # type: ignore[untyped-decorator]
def maintain_partitions(months_ahead: int = 2) -> None:
    """Pre-create upcoming month partitions for the workflow tables."""
    _post("/api/internal/workflows/maintain-partitions", {"months_ahead": months_ahead})
