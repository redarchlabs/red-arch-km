"""Ingest plumbing shared by the first-party documents router and the public API.

Both ``routers/documents.py`` (Clerk session) and ``POST /api/v1/knowledge/documents``
(API key) store a document, commit it, and enqueue the same worker ingest. The
pieces here are the ones that must not drift between the two: the upload type
allowlist, the bounded upload read, and recording the Celery task id.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from api import db_scope
from api.models.document import Document

logger = logging.getLogger(__name__)

# Extension allowlist for uploads. Extension is authoritative (a mislabelled
# Content-Type must not smuggle in an unsupported type). Kept in sync with the
# worker's extraction dispatcher.
ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
        ".gif",
        ".webp",
        ".txt",
        ".md",
        ".docx",
        ".doc",
    }
)

# Best-effort Content-Type per extension when the client omits/mislabels it.
EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


async def read_upload_bounded(file: UploadFile, max_file_size_mb: int) -> bytes:
    """Read an upload in 1 MiB chunks, aborting with 413 the moment it exceeds the cap.

    ``await file.read()`` (unbounded) would defeat the limit — Starlette does not
    cap file parts by size — so a malicious upload could spool gigabytes before
    being rejected. An empty file is a 400.
    """
    max_bytes = max_file_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1 << 20):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the {max_file_size_mb} MB limit",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty")
    return b"".join(chunks)


async def persist_task_id(session: AsyncSession, org_id: Any, doc: Any, task_id: str | None) -> None:
    """Write ``celery_task_id`` back in its own tenant-scoped transaction.

    Every caller dispatches only *after* committing the row, and that commit ends
    the transaction — taking the ``SET LOCAL ROLE`` and ``app.current_tenant_id``
    GUC with it. Assigning the attribute and leaving it for ``get_tenant_db``'s
    teardown commit does not work: that flush runs with no tenant context, so the
    RLS ``USING`` predicate is NULL, the UPDATE matches 0 rows, and SQLAlchemy
    raises ``StaleDataError``. Dependency teardown runs *after* the response has
    been sent, so the error cannot become a 500 — uvicorn drops the TCP
    connection instead, which the client sees as a socket hang up on its next
    keep-alive request, and the id is lost either way.

    Written as a Core UPDATE rather than by dirtying the instance: an ORM flush
    that matches no row raises, while this simply reports zero rows, and it
    leaves nothing pending for a later commit to trip over. The in-memory value
    is then set as *committed* state so the response carries it without the
    instance going dirty again.

    ``None`` is a legitimate value: a re-dispatch with nothing to enqueue clears
    the previous id so cancellation cannot target a task that no longer owns this
    document.

    Never raises. Losing the id costs cancellation and job-log correlation for
    this ingest; it must not fail a request whose row and task both exist.
    """
    document_id = doc.id
    try:
        await db_scope.enter_tenant(session, org_id)
        result = await session.execute(
            update(Document).where(Document.id == document_id).values(celery_task_id=task_id)
        )
        await session.commit()
        if int(getattr(result, "rowcount", 0) or 0) < 1:
            logger.error("celery_task_id not stored: document %s not visible in org %s", document_id, org_id)
            return
        set_committed_value(doc, "celery_task_id", task_id)
    except Exception:
        await session.rollback()
        logger.exception("Failed to persist celery_task_id for document %s", document_id)
