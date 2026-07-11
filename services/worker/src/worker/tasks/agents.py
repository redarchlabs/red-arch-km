"""Agent-org worker tasks.

Like the workflow tasks, these are thin ``httpx`` callbacks into the API's
internal endpoints — the API does the RLS-scoped work. ``advance_runs`` runs on
the celery-beat cadence to claim + drive queued agent runs (delegated children,
scheduled runs, and work-order runs).
"""

from __future__ import annotations

from typing import Any

from worker.celery_app import app
from worker.tasks.workflow import _post


@app.task  # type: ignore[untyped-decorator]  # celery's app.task is untyped
def advance_runs(limit: int = 20) -> dict[str, Any] | None:
    """Claim + drive a batch of queued agent runs via the API."""
    return _post("/api/internal/agents/advance-runs", {"limit": limit})
