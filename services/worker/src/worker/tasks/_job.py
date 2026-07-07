"""Ingest job telemetry: coarse progress, per-job logs, and cancellation.

Three concerns that ride alongside the ingest lifecycle, kept out of
``_ingest_common`` so the brain-POST logic stays focused:

* **Progress** — the worker reports coarse, stage-based ``PROCESSING`` updates
  (``{stage, percent}`` in ``processing_details``) so the UI can show a bar.
  brain-api's ingest is one blocking call, so percentages are worker-stage
  granularity, not per-chunk.
* **Per-job logs** — each stage appends a structured line to a capped Redis
  list keyed by ``document_id`` (``job:logs:{id}``), which the API serves back
  to the document reader and the site-admin console.
* **Cancellation** — the API's cancel endpoint sets ``ingest:cancel:{id}`` and
  revokes the task. The worker also checks the flag at every stage boundary so
  a redelivered task (Celery's revoke set is lost on worker restart) still
  aborts cleanly instead of finishing and clobbering the CANCELLED status.

All Redis keys live in the broker DB (db 0) so one connection serves the queue,
the beat heartbeat, and this. Key formats are mirrored by the API
(``services/api/src/api/routers/admin.py`` / ``documents.py``) — keep them in sync.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import redis as redis_sync

from worker.config import WorkerSettings
from worker.tasks._ingest_common import report_status

logger = logging.getLogger(__name__)

# --- Redis key formats (mirrored in the API service) ---
# Logs key on document_id (the reader wants a document's job history). Cancel
# keys on the Celery task id — unique per ingest and preserved across redelivery
# — so a stale flag can't abort a later re-ingest of the same document, and a
# revoke-lost-on-restart redelivery of the SAME task still sees the flag.
JOB_LOG_KEY = "job:logs:{document_id}"
INGEST_CANCEL_KEY = "ingest:cancel:{task_id}"
# Keep a bounded tail of a job's log so a pathological doc can't grow it without
# limit; long enough to see the whole coarse lifecycle plus retries.
_JOB_LOG_MAX = 500
_JOB_LOG_TTL_SECONDS = 24 * 3600


class IngestCancelled(Exception):
    """Raised at a stage boundary when the job has been cancelled."""


# --- Coarse pipeline stages + their nominal completion percentage ---
# Worker-side granularity only: the brain POST (chunk/embed/summarize/facts) is a
# single blocking call, so it spans one band (60 -> 90) rather than sub-steps.
STAGE_QUEUED = "queued"
STAGE_DOWNLOADING = "downloading"
STAGE_EXTRACTING = "extracting"
STAGE_INGESTING = "ingesting"
STAGE_DONE = "done"

STAGE_PERCENT: dict[str, int] = {
    STAGE_QUEUED: 5,
    STAGE_DOWNLOADING: 15,
    STAGE_EXTRACTING: 35,
    STAGE_INGESTING: 60,
    STAGE_DONE: 100,
}


def _broker_redis() -> redis_sync.Redis:
    url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    return redis_sync.Redis.from_url(url)


def job_log(document_id: str, message: str, *, level: str = "info", stage: str | None = None) -> None:
    """Append one structured log line to the job's capped Redis list.

    Best-effort and never raises — a monitoring write must not fail an ingest.
    Also mirrors to the worker's Python logger so stdout/OTel keep the line.
    """
    logger.info("[job %s] %s", document_id, message)
    if not document_id:
        return
    entry = json.dumps(
        {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "stage": stage,
            "message": message,
        }
    )
    key = JOB_LOG_KEY.format(document_id=document_id)
    client = _broker_redis()
    try:
        pipe = client.pipeline()
        pipe.rpush(key, entry)
        pipe.ltrim(key, -_JOB_LOG_MAX, -1)
        pipe.expire(key, _JOB_LOG_TTL_SECONDS)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — logging must never crash the worker
        logger.warning("job_log redis write failed for %s: %s", document_id, exc)
    finally:
        client.close()


def report_progress(
    settings: WorkerSettings,
    document_id: str,
    tenant_id: str,
    stage: str,
    *,
    message: str | None = None,
) -> None:
    """Report a coarse ``PROCESSING`` progress update and log the transition.

    Writes ``{stage, percent}`` into ``processing_details`` via the same status
    callback used for terminal states, and appends a job-log line.
    """
    percent = STAGE_PERCENT.get(stage, 0)
    report_status(
        settings,
        document_id,
        tenant_id,
        "PROCESSING",
        {"stage": stage, "percent": percent},
    )
    job_log(document_id, message or f"stage: {stage} ({percent}%)", stage=stage)


def is_cancelled(task_id: str) -> bool:
    """True if a cancel has been requested for this ingest task."""
    if not task_id:
        return False
    client = _broker_redis()
    try:
        return bool(client.exists(INGEST_CANCEL_KEY.format(task_id=task_id)))
    except Exception as exc:  # noqa: BLE001 — a broker hiccup must not block ingest
        logger.warning("cancel check failed for task %s: %s", task_id, exc)
        return False
    finally:
        client.close()


def raise_if_cancelled(settings: WorkerSettings, document_id: str, tenant_id: str, task_id: str) -> None:
    """Abort the ingest if cancellation was requested, marking it CANCELLED.

    Called at each stage boundary. Reports the terminal CANCELLED status (the
    cancel endpoint set it too, but reporting again is idempotent and covers the
    redelivered-task case) and raises ``IngestCancelled`` for the task to catch.
    """
    if not is_cancelled(task_id):
        return
    job_log(document_id, "ingest cancelled — aborting", level="warning", stage="cancelled")
    report_status(settings, document_id, tenant_id, "CANCELLED", {"stage": "cancelled"})
    raise IngestCancelled(task_id)
