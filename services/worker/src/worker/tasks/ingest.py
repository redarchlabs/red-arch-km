"""Document ingestion task: POST to brain-api with retry on transient errors only."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from worker.celery_app import app
from worker.config import WorkerSettings

logger = logging.getLogger(__name__)


def _is_retryable_http_error(exc: httpx.HTTPStatusError) -> bool:
    """Retry on 5xx and 429 (rate limiting) only. 4xx are client bugs."""
    status_code = exc.response.status_code
    return status_code >= 500 or status_code == 429


def _report_status(
    settings: WorkerSettings,
    document_id: str,
    tenant_id: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort status callback to the api service.

    The worker should never fail an ingest because the callback failed —
    so we swallow errors and log. The api service's processing_status may
    be stale in that case, which is less bad than losing successful work.
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
        logger.warning(
            "Status callback failed for document %s (%s): %s",
            document_id,
            status,
            e,
        )


@app.task(  # type: ignore[untyped-decorator]  # celery's app.task is untyped
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def task_ingest_document(self: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Ingest a document via the brain-api.

    Expected data keys:
        document_id, tenant_id, document_key, title, text, tags,
        access_keys, use_knowledge_graph, metadata
    """
    settings = WorkerSettings()
    document_id = data.get("document_id", "")
    document_key = data.get("document_key", "")
    tenant_id = data.get("tenant_id", "")

    logger.info("Ingesting document %s for tenant %s", document_key, tenant_id)

    # Report PROCESSING on first attempt only — retries shouldn't flip
    # the row back from a stale "FAILED" state of a prior run, and the
    # value is already PROCESSING on retry anyway.
    if self.request.retries == 0:
        _report_status(settings, document_id, tenant_id, "PROCESSING")

    try:
        response = httpx.post(
            f"{settings.brain_api_url}/api/ingest-document",
            json=data,
            headers={"X-API-Key": settings.brain_api_key},
            timeout=300,
        )
        response.raise_for_status()

    except httpx.HTTPStatusError as e:
        if _is_retryable_http_error(e):
            logger.warning(
                "Transient brain-api error for %s (HTTP %d); retrying",
                document_key,
                e.response.status_code,
            )
            # If this is the last retry, mark FAILED before Celery gives up.
            if self.request.retries >= self.max_retries:
                _report_status(
                    settings,
                    document_id,
                    tenant_id,
                    "FAILED",
                    {"error": f"HTTP {e.response.status_code}", "retries_exhausted": True},
                )
            raise self.retry(exc=e) from e
        logger.error(
            "Permanent brain-api error for %s (HTTP %d): %s",
            document_key,
            e.response.status_code,
            e.response.text[:500],
        )
        _report_status(
            settings,
            document_id,
            tenant_id,
            "FAILED",
            {"error": f"HTTP {e.response.status_code}", "body": e.response.text[:500]},
        )
        return {"status": "failed", "document_key": document_key, "error": str(e)}

    except (httpx.TimeoutException, httpx.NetworkError) as e:
        logger.warning("Network error ingesting %s: %s", document_key, e)
        if self.request.retries >= self.max_retries:
            _report_status(
                settings,
                document_id,
                tenant_id,
                "FAILED",
                {"error": "network", "message": str(e)},
            )
        raise self.retry(exc=e) from e

    result: dict[str, Any] = response.json()
    logger.info(
        "Document %s ingested: %d chunks, %d triplets",
        document_key,
        result.get("chunks", 0),
        result.get("triplets", 0),
    )
    _report_status(
        settings,
        document_id,
        tenant_id,
        "SUCCESS",
        {"chunks": result.get("chunks", 0), "triplets": result.get("triplets", 0)},
    )
    return {"status": "success", **result}
