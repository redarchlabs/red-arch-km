"""Celery task signatures for dispatching from the API service.

The actual task bodies live in the worker service. These signatures let the
API enqueue work without importing worker code.
"""

from __future__ import annotations

from typing import Any

from api.tasks.celery_app import celery_app


def dispatch_ingest(data: dict[str, Any]) -> str:
    """Enqueue a document ingestion. Returns the Celery task ID."""
    result = celery_app.send_task("worker.tasks.ingest.task_ingest_document", args=[data])
    return str(result.id)


def dispatch_extract_ingest(data: dict[str, Any]) -> str:
    """Enqueue an upload for extraction (OCR / AI vision) then ingestion.

    The worker downloads the stored original, extracts text, and only then
    POSTs to brain-api — the brain contract stays text-only. Secrets (the
    per-org OpenAI key) are NEVER put in this payload; the worker resolves
    them via the internal API. Returns the Celery task ID.
    """
    result = celery_app.send_task("worker.tasks.extract.task_extract_and_ingest", args=[data])
    return str(result.id)


def dispatch_metadata_update(data: dict[str, Any]) -> str:
    result = celery_app.send_task("worker.tasks.metadata.task_update_document_metadata", args=[data])
    return str(result.id)
