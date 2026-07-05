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

    logger.info("Ingesting document %s for tenant %s", document_key, tenant_id)

    report_status(settings, document_id, tenant_id, "PROCESSING")
    return post_to_brain_and_report(
        settings,
        data,
        document_id=document_id,
        document_key=document_key,
        tenant_id=tenant_id,
    )
