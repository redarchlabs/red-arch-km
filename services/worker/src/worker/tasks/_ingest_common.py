"""Shared brain-api submit/poll + status-callback logic for ingest tasks.

Both ``tasks.ingest`` (text docs) and ``tasks.extract`` (uploaded files, after
OCR/AI extraction) hand the SAME text-only payload to brain-api and report the
same PROCESSING/SUCCESS/FAILED lifecycle. That block lives here so it is not
duplicated.

brain-api ingestion is ASYNCHRONOUS: a large document's pipeline (chunk → embed
→ summarize → fact-extract over thousands of chunks) can run for tens of minutes,
far past any sane HTTP timeout. So this helper SUBMITS the document (a fast call
that returns 202) and then POLLS ``GET /ingest-status`` until the background job
finishes. Nothing holds a long HTTP request open, so ingestion time is decoupled
from any client timeout.

Submit is retried in-process (a bounded loop with backoff) rather than via
Celery's ``self.retry``: the extract task must OCR the file exactly once — re-
running the whole Celery task on a transient error would re-run the (paid) OCR
step. Crucially we do NOT retry a submit *read timeout* by blindly re-POSTing —
brain-api may already be processing it — the poll loop discovers the outcome.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from worker.config import WorkerSettings

logger = logging.getLogger(__name__)

# Consecutive status-poll transport errors tolerated before declaring failure —
# rides out a brief brain-api blip without abandoning a running ingest.
_MAX_STATUS_ERRORS = 5
# Consecutive "unknown" states tolerated. brain-api marks the job running before
# it returns 202, so a persistent "unknown" means it lost the job (a restart) —
# treat as failed so the task can be re-dispatched rather than polling forever.
_MAX_UNKNOWN_POLLS = 3


def is_retryable_http_error(exc: httpx.HTTPStatusError) -> bool:
    """Retry on 5xx and 429 (rate limiting) only. 4xx are client bugs."""
    status_code = exc.response.status_code
    return status_code >= 500 or status_code == 429


def report_status(
    settings: WorkerSettings,
    document_id: str,
    tenant_id: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort status callback to the api service.

    The worker should never fail an ingest because the callback failed — so we
    swallow errors and log. A stale processing_status is less bad than losing
    successful work.
    """
    if not document_id:
        return
    if not settings.internal_api_key:
        # Misconfiguration: without the key the worker can't report status, so
        # documents would sit at PENDING forever with no signal. Make it loud.
        logger.warning(
            "INTERNAL_API_KEY not set — cannot report '%s' for document %s; it will appear stuck",
            status,
            document_id,
        )
        return
    try:
        response = httpx.post(
            f"{settings.api_url.rstrip('/')}/api/internal/documents/{document_id}/status",
            json={
                "tenant_id": tenant_id,
                "status": status,
                "details": details or {},
            },
            headers={"X-Internal-API-Key": settings.internal_api_key},
            timeout=15,
        )
        response.raise_for_status()
    except Exception as e:
        logger.warning("Status callback failed for document %s (%s): %s", document_id, status, e)


def post_to_brain_and_report(
    settings: WorkerSettings,
    data: dict[str, Any],
    *,
    document_id: str,
    document_key: str,
    tenant_id: str,
    task_id: str = "",
    max_retries: int = 3,
    retry_delay: int = 30,
) -> dict[str, Any]:
    """Submit the document to brain-api, poll to completion, then report status.

    Two phases: (1) submit — a fast POST that returns 202 once brain-api accepts
    the job; (2) poll — ``GET /ingest-status`` until the background pipeline is
    done/failed. Reports SUCCESS/FAILED via the api callback. Does NOT report
    PROCESSING — the caller does that once up front (so a re-OCR never re-flips
    the status). A cancel (Redis flag) at any point aborts cleanly.
    """
    submit_failure = _submit_ingest(
        settings,
        data,
        document_id=document_id,
        document_key=document_key,
        tenant_id=tenant_id,
        task_id=task_id,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    if submit_failure is not None:
        return submit_failure

    return _poll_until_complete(
        settings,
        document_id=document_id,
        document_key=document_key,
        tenant_id=tenant_id,
        task_id=task_id,
    )


def _submit_ingest(
    settings: WorkerSettings,
    data: dict[str, Any],
    *,
    document_id: str,
    document_key: str,
    tenant_id: str,
    task_id: str,
    max_retries: int,
    retry_delay: int,
) -> dict[str, Any] | None:
    """POST the payload to brain-api's async ingest endpoint (expects 202).

    Returns ``None`` once accepted. Returns a terminal result dict (and reports
    FAILED/CANCELLED) if the submit can't be accepted. Retries transient 5xx/429
    and connection errors. A read timeout on submit is NOT retried by re-POSTing
    (brain-api may already be processing it) — it's reported as failed so the
    task can be re-dispatched rather than silently double-submitting.
    """
    from worker.tasks._job import IngestCancelled, job_log, raise_if_cancelled

    attempt = 0
    while True:
        try:
            raise_if_cancelled(settings, document_id, tenant_id, task_id)
        except IngestCancelled:
            return {"status": "cancelled", "document_key": document_key}

        try:
            response = httpx.post(
                f"{settings.brain_api_url}/api/ingest-document",
                json=data,
                headers={"X-API-Key": settings.brain_api_key},
                timeout=settings.brain_ingest_accept_timeout_seconds,
            )
            response.raise_for_status()
            return None

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if is_retryable_http_error(e) and attempt < max_retries:
                logger.warning(
                    "Transient brain-api error submitting %s (HTTP %d); retry %d/%d",
                    document_key,
                    status_code,
                    attempt + 1,
                    max_retries,
                )
                attempt += 1
                time.sleep(retry_delay)
                continue
            detail = (
                {"error": f"HTTP {status_code}", "retries_exhausted": True}
                if is_retryable_http_error(e)
                else {"error": f"HTTP {status_code}", "body": e.response.text[:500]}
            )
            logger.error("brain-api submit failed for %s (HTTP %d)", document_key, status_code)
            job_log(document_id, f"brain-api submit failed (HTTP {status_code})", level="error", stage="failed")
            report_status(settings, document_id, tenant_id, "FAILED", detail)
            return {"status": "failed", "document_key": document_key, "error": str(e)}

        except httpx.ConnectError as e:
            # Nothing was processed (connection never established) → safe to retry.
            if attempt < max_retries:
                logger.warning(
                    "Cannot reach brain-api for %s: %s; retry %d/%d", document_key, e, attempt + 1, max_retries
                )
                attempt += 1
                time.sleep(retry_delay)
                continue
            logger.error("brain-api unreachable submitting %s: %s", document_key, e)
            job_log(document_id, f"network error reaching brain-api: {e}", level="error", stage="failed")
            report_status(settings, document_id, tenant_id, "FAILED", {"error": "network", "message": str(e)})
            return {"status": "failed", "document_key": document_key, "error": str(e)}

        except httpx.TimeoutException as e:
            # A submit that timed out may already be processing on brain-api — do
            # NOT re-POST (that would double-ingest). Fail so the task re-dispatches.
            logger.error("brain-api submit timed out for %s: %s", document_key, e)
            job_log(document_id, f"brain-api submit timed out: {e}", level="error", stage="failed")
            report_status(settings, document_id, tenant_id, "FAILED", {"error": "submit_timeout", "message": str(e)})
            return {"status": "failed", "document_key": document_key, "error": str(e)}


# Brain-api ingest progress (0..1) maps into this worker-side percent band. 60 is
# STAGE_INGESTING (reported before the brain call); the ceiling stays below 100 so
# only the terminal SUCCESS callback shows a completed bar.
_INGEST_BAND_START = 60
_INGEST_BAND_END = 97


def _relay_progress(
    settings: WorkerSettings,
    body: dict[str, Any],
    *,
    document_id: str,
    tenant_id: str,
    last_percent: int,
    last_phase: str,
) -> tuple[int, str]:
    """Translate a running brain-api poll into a PROCESSING status update.

    Maps ``progress`` (0..1) into the ingest percent band and reports it only
    when the percent advances or the phase changes, so a long-running ingest
    keeps its bar moving without flooding the status endpoint. Returns the
    (percent, phase) to carry into the next poll. Best-effort — a bad/missing
    field just leaves the bar where it was.
    """
    from worker.tasks._job import job_log

    raw = body.get("progress")
    fraction = raw if isinstance(raw, (int, float)) else 0.0
    fraction = max(0.0, min(1.0, float(fraction)))
    phase = body.get("phase") or "ingesting"
    percent = _INGEST_BAND_START + round(fraction * (_INGEST_BAND_END - _INGEST_BAND_START))
    percent = max(last_percent, min(_INGEST_BAND_END, percent))

    if percent == last_percent and phase == last_phase:
        return last_percent, last_phase

    # Stage carries the sub-phase so both the document view and the site-admin
    # console show what's happening (they fall back to the raw stage string).
    report_status(settings, document_id, tenant_id, "PROCESSING", {"stage": phase, "percent": percent})
    if phase != last_phase:
        job_log(document_id, f"{phase} ({percent}%)", stage=phase)
    return percent, phase


def _poll_until_complete(
    settings: WorkerSettings,
    *,
    document_id: str,
    document_key: str,
    tenant_id: str,
    task_id: str,
) -> dict[str, Any]:
    """Poll ``GET /ingest-status`` until the background ingest is done/failed.

    Reports SUCCESS with chunk/claim counts, or FAILED on a failed job, a lost
    job (brain-api restart → persistent "unknown"), repeated transport errors,
    or the ``max_wait`` ceiling. A cancel flag between polls aborts cleanly.
    """
    from worker.tasks._job import IngestCancelled, job_log, raise_if_cancelled

    deadline = time.monotonic() + settings.brain_ingest_max_wait_seconds
    error_streak = 0
    unknown_streak = 0
    # STAGE_INGESTING is 60% up front; brain-api's pipeline progress (0..1) maps
    # into the 60→97 band so the bar climbs while the background job runs instead
    # of sitting frozen at 60. Track the last-reported values to avoid redundant
    # status callbacks (and log spam) between unchanged polls.
    last_percent = 60
    last_phase = ""

    while True:
        try:
            raise_if_cancelled(settings, document_id, tenant_id, task_id)
        except IngestCancelled:
            # Note: brain-api's background job isn't interrupted; its result is
            # orphaned and replaced on the next reprocess (ingest is purge-first).
            return {"status": "cancelled", "document_key": document_key}

        if time.monotonic() > deadline:
            logger.error("Ingest of %s exceeded max wait (%ds)", document_key, settings.brain_ingest_max_wait_seconds)
            job_log(document_id, "ingest exceeded max wait; giving up", level="error", stage="failed")
            report_status(settings, document_id, tenant_id, "FAILED", {"error": "max_wait_exceeded"})
            return {"status": "failed", "document_key": document_key, "error": "max_wait_exceeded"}

        try:
            response = httpx.get(
                f"{settings.brain_api_url}/api/ingest-status/{tenant_id}/{document_key}",
                headers={"X-API-Key": settings.brain_api_key},
                timeout=settings.brain_ingest_accept_timeout_seconds,
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except Exception as e:  # noqa: BLE001 — tolerate transient poll errors
            error_streak += 1
            if error_streak > _MAX_STATUS_ERRORS:
                logger.error("Giving up polling brain-api for %s after repeated errors: %s", document_key, e)
                job_log(document_id, f"lost contact with brain-api: {e}", level="error", stage="failed")
                report_status(settings, document_id, tenant_id, "FAILED", {"error": "poll_failed", "message": str(e)})
                return {"status": "failed", "document_key": document_key, "error": str(e)}
            time.sleep(settings.brain_ingest_poll_interval_seconds)
            continue
        error_streak = 0

        state = body.get("state")
        if state == "done":
            chunks = int(body.get("chunks", 0))
            triplets = int(body.get("triplets", 0))
            logger.info("Document %s ingested: %d chunks, %d triplets", document_key, chunks, triplets)
            job_log(document_id, f"ingest complete: {chunks} chunks, {triplets} claims", stage="done")
            report_status(settings, document_id, tenant_id, "SUCCESS", {"chunks": chunks, "triplets": triplets})
            return {"status": "success", "document_key": document_key, "chunks": chunks, "triplets": triplets}

        if state == "failed":
            error = body.get("error") or "ingestion failed"
            logger.error("brain-api reported ingest failed for %s: %s", document_key, error)
            job_log(document_id, f"brain-api ingest failed: {error}", level="error", stage="failed")
            report_status(settings, document_id, tenant_id, "FAILED", {"error": "ingest_failed", "message": error})
            return {"status": "failed", "document_key": document_key, "error": error}

        if state == "unknown":
            unknown_streak += 1
            if unknown_streak > _MAX_UNKNOWN_POLLS:
                logger.error("brain-api lost ingest job for %s (restart?)", document_key)
                job_log(document_id, "brain-api lost the ingest job", level="error", stage="failed")
                report_status(settings, document_id, tenant_id, "FAILED", {"error": "job_lost"})
                return {"status": "failed", "document_key": document_key, "error": "job_lost"}
        else:
            unknown_streak = 0  # still running
            last_percent, last_phase = _relay_progress(
                settings,
                body,
                document_id=document_id,
                tenant_id=tenant_id,
                last_percent=last_percent,
                last_phase=last_phase,
            )

        time.sleep(settings.brain_ingest_poll_interval_seconds)
