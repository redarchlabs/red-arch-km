"""Document ingestion task: POST already-extracted text to brain-api.

The brain-POST + retry + status-callback logic lives in ``_ingest_common`` and
is shared with ``tasks.extract`` (uploaded files, after OCR/AI extraction).
"""

from __future__ import annotations

import logging
from typing import Any

from worker.celery_app import app
from worker.config import WorkerSettings
from worker.tasks._ingest_common import (
    is_retryable_http_error,
    post_to_brain_and_report,
    report_status,
)
from worker.tasks._job import (
    STAGE_INGESTING,
    STAGE_QUEUED,
    IngestCancelled,
    raise_if_cancelled,
    report_progress,
)

logger = logging.getLogger(__name__)

# Re-exported for backwards compatibility (tests import this symbol here).
_is_retryable_http_error = is_retryable_http_error
_report_status = report_status


@app.task(  # type: ignore[untyped-decorator]  # celery's app.task is untyped
    bind=True,
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
    task_id = self.request.id or ""

    logger.info("Ingesting document %s for tenant %s", document_key, tenant_id)

    report_progress(settings, document_id, tenant_id, STAGE_QUEUED, message=f"queued: {document_key}")
    try:
        raise_if_cancelled(settings, document_id, tenant_id, task_id)
    except IngestCancelled:
        return {"status": "cancelled", "document_key": document_key}

    # No extract stage for text docs — go straight to the brain ingest band.
    report_progress(settings, document_id, tenant_id, STAGE_INGESTING, message="sending text to brain-api")
    return post_to_brain_and_report(
        settings,
        data,
        document_id=document_id,
        document_key=document_key,
        tenant_id=tenant_id,
        task_id=task_id,
    )
