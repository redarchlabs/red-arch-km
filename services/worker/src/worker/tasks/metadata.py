"""Document metadata update task."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from worker.celery_app import app
from worker.config import WorkerSettings

logger = logging.getLogger(__name__)


def _is_retryable_http_error(exc: httpx.HTTPStatusError) -> bool:
    status_code = exc.response.status_code
    return status_code >= 500 or status_code == 429


@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def task_update_document_metadata(self: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Update document metadata (tags, access rules, title) via brain-api.

    Expected data keys:
        tenant_id, document_key, new_tags, new_access_keys, title
    """
    settings = WorkerSettings()  # type: ignore[call-arg]
    document_key = data.get("document_key", "")

    try:
        response = httpx.post(
            f"{settings.brain_api_url}/api/update-document-metadata",
            json=data,
            headers={"X-API-Key": settings.brain_api_key},
            timeout=60,
        )
        response.raise_for_status()

    except httpx.HTTPStatusError as e:
        if _is_retryable_http_error(e):
            logger.warning("Transient error updating %s; retrying", document_key)
            raise self.retry(exc=e) from e
        logger.error(
            "Permanent error updating metadata for %s: %s",
            document_key, e.response.text[:500],
        )
        return {"status": "failed", "document_key": document_key, "error": str(e)}

    except (httpx.TimeoutException, httpx.NetworkError) as e:
        logger.warning("Network error updating %s: %s", document_key, e)
        raise self.retry(exc=e) from e

    logger.info("Metadata updated for document %s", document_key)
    return {"status": "success", "document_key": document_key}
