"""Extract-and-ingest task for uploaded files.

Flow: report PROCESSING → download the stored original → resolve the OpenAI key
(only for the ``ai`` method) → extract text (OCR or vision) → POST the extracted
text to brain-api via the shared ingest helper.

Extraction runs exactly once. The brain POST retries in-process (see
``_ingest_common``) so a downstream brain-api 5xx never re-runs the paid OCR.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from worker.celery_app import app
from worker.config import WorkerSettings
from worker.extract import extract_text
from worker.storage import StorageClient
from worker.tasks._ingest_common import post_to_brain_and_report, report_status

logger = logging.getLogger(__name__)


def _resolve_openai_key(settings: WorkerSettings, tenant_id: str) -> str | None:
    """Resolve the per-org OpenAI key via the internal API, else the central key.

    The key is never carried in the Celery payload — the worker fetches it here
    so the secret stays off the broker. On any lookup failure we fall back to the
    central ``OPENAI_API_KEY`` rather than failing the whole extraction.
    """
    org_key: str | None = None
    if settings.internal_api_key and tenant_id:
        try:
            response = httpx.get(
                f"{settings.api_url.rstrip('/')}/api/internal/orgs/{tenant_id}/openai-key",
                headers={"X-Internal-API-Key": settings.internal_api_key},
                timeout=15,
            )
            response.raise_for_status()
            org_key = response.json().get("openai_api_key")
        except Exception as e:
            logger.warning("Per-org OpenAI key lookup failed for tenant %s: %s", tenant_id, e)

    return org_key or settings.openai_api_key or None


@app.task(  # type: ignore[untyped-decorator]  # celery's app.task is untyped
    bind=True,
    acks_late=True,
)
def task_extract_and_ingest(self: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Download an uploaded original, extract its text, then ingest it.

    Expected data keys:
        document_id, tenant_id, document_key, document_url (storage key),
        filename, title, translation_method, tags, access_keys,
        use_knowledge_graph, metadata
    """
    settings = WorkerSettings()
    document_id = data.get("document_id", "")
    document_key = data.get("document_key", "")
    tenant_id = data.get("tenant_id", "")
    object_key = data.get("document_url", "")
    filename = data.get("filename", "")
    method = data.get("translation_method", "ocr")

    logger.info("Extracting %s (method=%s) for tenant %s", filename, method, tenant_id)
    report_status(settings, document_id, tenant_id, "PROCESSING")

    # --- Extraction (runs exactly once; failures are terminal, not retried) ---
    try:
        raw = StorageClient(settings).get_object(object_key)
        openai_key = _resolve_openai_key(settings, tenant_id) if method == "ai" else None
        text = extract_text(raw, filename, method, openai_key)
    except Exception as e:
        logger.exception("Extraction failed for %s (%s)", document_key, filename)
        report_status(
            settings,
            document_id,
            tenant_id,
            "FAILED",
            {"stage": "extraction", "method": method, "error": str(e)},
        )
        return {"status": "failed", "document_key": document_key, "error": str(e)}

    if not text.strip():
        logger.warning("Extraction produced empty text for %s (%s)", document_key, filename)

    # --- Brain ingest (text-only payload; brain contract unchanged) ---
    brain_payload = {
        "document_id": document_id,
        "tenant_id": tenant_id,
        "document_key": document_key,
        "title": data.get("title", filename),
        "text": text,
        "tags": data.get("tags", []),
        "access_keys": data.get("access_keys", []),
        "use_knowledge_graph": data.get("use_knowledge_graph", True),
        "metadata": data.get("metadata", {}),
    }
    return post_to_brain_and_report(
        settings,
        brain_payload,
        document_id=document_id,
        document_key=document_key,
        tenant_id=tenant_id,
    )
