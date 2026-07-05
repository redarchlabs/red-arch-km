"""Shared brain-api POST + status-callback logic for ingest tasks.

Both ``tasks.ingest`` (text docs) and ``tasks.extract`` (uploaded files, after
OCR/AI extraction) POST the SAME text-only payload to brain-api and report the
same PROCESSING/SUCCESS/FAILED lifecycle. That block lives here so it is not
duplicated.

Retries happen IN-PROCESS here (a bounded loop with backoff) rather than via
Celery's ``self.retry``. This is deliberate: the extract task must OCR the file
exactly once — re-running the whole Celery task on a brain-api 5xx would re-run
the (paid) OCR step. Keeping retries inside this helper means the caller does
extraction once and this function retries only the brain POST.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from worker.config import WorkerSettings

logger = logging.getLogger(__name__)


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
    if not document_id or not settings.internal_api_key:
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
    max_retries: int = 3,
    retry_delay: int = 30,
) -> dict[str, Any]:
    """POST the ingest payload to brain-api with in-process retries, then report.

    Reports SUCCESS on a 2xx, FAILED on a permanent 4xx or once retries are
    exhausted. Returns a small result dict. Does NOT report PROCESSING — the
    caller does that once up front (so a re-OCR never re-flips the status).
    """
    attempt = 0
    while True:
        try:
            response = httpx.post(
                f"{settings.brain_api_url}/api/ingest-document",
                json=data,
                headers={"X-API-Key": settings.brain_api_key},
                timeout=300,
            )
            response.raise_for_status()
            break

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if is_retryable_http_error(e) and attempt < max_retries:
                logger.warning(
                    "Transient brain-api error for %s (HTTP %d); retry %d/%d",
                    document_key,
                    status_code,
                    attempt + 1,
                    max_retries,
                )
                attempt += 1
                time.sleep(retry_delay)
                continue
            if is_retryable_http_error(e):
                logger.error("brain-api retries exhausted for %s (HTTP %d)", document_key, status_code)
                report_status(
                    settings,
                    document_id,
                    tenant_id,
                    "FAILED",
                    {"error": f"HTTP {status_code}", "retries_exhausted": True},
                )
            else:
                logger.error(
                    "Permanent brain-api error for %s (HTTP %d): %s",
                    document_key,
                    status_code,
                    e.response.text[:500],
                )
                report_status(
                    settings,
                    document_id,
                    tenant_id,
                    "FAILED",
                    {"error": f"HTTP {status_code}", "body": e.response.text[:500]},
                )
            return {"status": "failed", "document_key": document_key, "error": str(e)}

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt < max_retries:
                logger.warning("Network error ingesting %s: %s; retry %d/%d", document_key, e, attempt + 1, max_retries)
                attempt += 1
                time.sleep(retry_delay)
                continue
            logger.error("Network retries exhausted ingesting %s: %s", document_key, e)
            report_status(
                settings,
                document_id,
                tenant_id,
                "FAILED",
                {"error": "network", "message": str(e)},
            )
            return {"status": "failed", "document_key": document_key, "error": str(e)}

    result: dict[str, Any] = response.json()
    logger.info(
        "Document %s ingested: %d chunks, %d triplets",
        document_key,
        result.get("chunks", 0),
        result.get("triplets", 0),
    )
    report_status(
        settings,
        document_id,
        tenant_id,
        "SUCCESS",
        {"chunks": result.get("chunks", 0), "triplets": result.get("triplets", 0)},
    )
    return {"status": "success", **result}
