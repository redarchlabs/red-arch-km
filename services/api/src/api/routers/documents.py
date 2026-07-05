"""Document CRUD routes."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.document import Tag
from api.models.org import Org
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.schemas.common import PaginatedResponse, PaginationParams
from api.schemas.document import DocumentCreate, DocumentRead, DocumentUpdate
from api.services.brain_client import BrainAPIClient
from api.services.permission_config import calculate_user_masks_from_membership
from api.services.storage import StorageClient
from api.tasks.ingest import dispatch_extract_ingest, dispatch_ingest

logger = logging.getLogger(__name__)
router = APIRouter()

# Extension allowlist for uploads. Extension is authoritative (a mislabelled
# Content-Type must not smuggle in an unsupported type). Kept in sync with the
# worker's extraction dispatcher.
_ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".txt", ".md"}
)

# Best-effort Content-Type per extension when the client omits/mislabels it.
_EXTENSION_CONTENT_TYPES: dict[str, str] = {
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
}

_VALID_TRANSLATION_METHODS: frozenset[str] = frozenset({"ocr", "ai"})


@router.get("/", response_model=PaginatedResponse[DocumentRead])
async def list_documents(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
    folder_id: Annotated[uuid.UUID | None, Query(description="Scope to a single folder's contents")] = None,
) -> PaginatedResponse[DocumentRead]:
    """List documents the user can view through folder permissions.

    Without ``folder_id`` this returns every document in a folder the user can
    see. Unfiled documents (``folder_id IS NULL``) bypass the folder permission
    system, so they are surfaced only to org admins — this both fixes the bug
    where pasted/unfiled docs were invisible to everyone AND avoids exposing an
    unfiled doc to every member. (Matches the Go handler's ``isAdmin`` param.)
    Regular members should file documents into a folder via the picker. With
    ``folder_id`` the list is scoped to exactly that folder's contents (the
    caller must be able to see the folder).
    """
    folder_repo = FolderRepository(session)
    doc_repo = DocumentRepository(session)

    if ctx.is_org_admin:
        visible_folders, _ = await folder_repo.list_visible_to_masks(user_masks=None)
    else:
        org = await session.get(Org, ctx.org_id)
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        user_masks = calculate_user_masks_from_membership(ctx.membership, org.permission_number)
        visible_folders, _ = await folder_repo.list_visible_to_masks(user_masks=user_masks)

    visible_ids = [f.id for f in visible_folders]

    if folder_id is not None:
        # Scoped browse: only that folder, and only if the caller can see it.
        if folder_id not in visible_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found or not visible",
            )
        documents, total = await doc_repo.list_for_folders(
            folder_ids=[folder_id],
            include_unfiled=False,
            offset=pagination.offset,
            limit=pagination.page_size,
        )
    else:
        # Unfiled docs bypass folder permissions → only admins see them.
        documents, total = await doc_repo.list_for_folders(
            folder_ids=visible_ids,
            include_unfiled=ctx.is_org_admin,
            offset=pagination.offset,
            limit=pagination.page_size,
        )

    pages = (total + pagination.page_size - 1) // pagination.page_size
    return PaginatedResponse[DocumentRead](
        items=[DocumentRead.model_validate(d) for d in documents],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.post("/", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentCreate,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentRead:
    """Create a document and enqueue it for ingestion."""
    doc_repo = DocumentRepository(session)
    folder_repo = FolderRepository(session)

    # Validate folder BEFORE persisting the document, so we reject bad
    # folder_ids (typo / stale client cache / cross-tenant via RLS) with a
    # clean 400 instead of saving a doc that references nothing resolvable.
    access_keys: list[int] = []
    tag_names: list[str] = []
    if body.folder_id is not None:
        folder = await folder_repo.get(body.folder_id)
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id does not exist in this organization",
            )
        access_keys = list(folder.view_permission_masks or [])
        # Encode folder membership as a tag for graph-level filtering
        tag_names.append(f"folder:{folder.id}")

    doc = await doc_repo.create(
        title=body.title,
        org_id=ctx.org_id,
        text=body.text,
        description=body.description,
        folder_id=body.folder_id,
        uploaded_by_id=ctx.user.profile_id,
        use_knowledge_graph=body.use_knowledge_graph,
        metadata=body.metadata,
        tag_ids=body.tag_ids,
    )

    # Commit before dispatching so the worker can read the row via the API
    await session.commit()

    if doc.text:
        # The document row is already committed. Enqueueing talks to the Celery
        # broker synchronously, so a broker outage must not turn a successful
        # create into a 500 that tells the user creation failed (it didn't —
        # the row exists). Swallow the enqueue error, leave the doc PENDING so a
        # future reconciliation sweep can re-dispatch it, and still return 201.
        try:
            task_id = dispatch_ingest(
                {
                    "document_id": str(doc.id),
                    "tenant_id": str(ctx.org_id),
                    "document_key": doc.document_key,
                    "title": doc.title,
                    "text": doc.text,
                    "tags": tag_names,
                    "access_keys": access_keys,
                    "use_knowledge_graph": doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True,
                    "metadata": doc.metadata_ or {},
                }
            )
            logger.info("Document %s queued for ingestion (task %s)", doc.id, task_id)
        except Exception:
            logger.exception(
                "Document %s created but ingestion enqueue failed; left PENDING for retry",
                doc.id,
            )
    else:
        logger.info("Document %s created without text; skipping ingestion", doc.id)

    return DocumentRead.model_validate(doc)


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="The file to ingest (pdf/image/text).")],
    title: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    folder_id: Annotated[str | None, Form()] = None,
    translation_method: Annotated[str, Form()] = "ocr",
) -> DocumentRead:
    """Upload a file, store the original, and enqueue extraction + ingestion.

    Text is extracted in the worker (OCR via Tesseract, or OpenAI vision when
    ``translation_method="ai"``) BEFORE calling brain-api — the brain contract
    stays text-only. The stored object key lives in ``document_url``.
    """
    # --- Boundary validation FIRST (before any storage/DB work) ---
    if translation_method not in _VALID_TRANSLATION_METHODS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"translation_method must be one of {sorted(_VALID_TRANSLATION_METHODS)}",
        )

    filename = os.path.basename(file.filename or "")
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A filename is required")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext or filename}'. Allowed: {sorted(_ALLOWED_UPLOAD_EXTENSIONS)}",
        )

    # Read in bounded chunks and abort the moment we exceed the cap, so a
    # malicious/huge upload can't spool gigabytes to disk/memory before being
    # rejected. `await file.read()` (unbounded) would defeat the limit — Starlette
    # does not cap file parts by size.
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1 << 20):  # 1 MiB at a time
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the {settings.max_file_size_mb} MB limit",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty")
    content = b"".join(chunks)

    # --- Folder validation + permission masks (mirrors create_document) ---
    folder_uuid: uuid.UUID | None = None
    access_keys: list[int] = []
    tag_names: list[str] = []
    if folder_id:
        try:
            folder_uuid = uuid.UUID(folder_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id is not a valid UUID",
            ) from e
        folder_repo = FolderRepository(session)
        folder = await folder_repo.get(folder_uuid)
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id does not exist in this organization",
            )
        access_keys = list(folder.view_permission_masks or [])
        tag_names.append(f"folder:{folder.id}")

    # --- Persist the row (text=None; extraction fills it later) ---
    doc_repo = DocumentRepository(session)
    doc = await doc_repo.create(
        title=title,
        org_id=ctx.org_id,
        text=None,
        description=description,
        folder_id=folder_uuid,
        uploaded_by_id=ctx.user.profile_id,
    )

    object_key = f"{ctx.org_id}/{doc.document_key}/{filename}"
    content_type = file.content_type or _EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")

    # Store the original BEFORE commit so a storage outage rolls back the row
    # (no dangling PENDING doc pointing at a missing object).
    try:
        StorageClient(settings).put_object(object_key, content, content_type)
    except Exception as e:
        logger.exception("Failed to store upload for org %s (%s)", ctx.org_id, filename)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store the uploaded file",
        ) from e

    doc.document_url = object_key
    await session.commit()

    # Enqueue extraction. Mirror create_document: a broker outage must not turn
    # a successful upload into a 500 — the row + object exist, leave it PENDING.
    try:
        task_id = dispatch_extract_ingest(
            {
                "document_id": str(doc.id),
                "tenant_id": str(ctx.org_id),
                "document_key": doc.document_key,
                "document_url": object_key,
                "filename": filename,
                "title": doc.title,
                "translation_method": translation_method,
                "tags": tag_names,
                "access_keys": access_keys,
                "use_knowledge_graph": doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True,
                "metadata": doc.metadata_ or {},
            }
        )
        logger.info("Upload %s queued for extraction+ingestion (task %s)", doc.id, task_id)
    except Exception:
        logger.exception(
            "Upload %s stored but extraction enqueue failed; left PENDING for retry",
            doc.id,
        )

    return DocumentRead.model_validate(doc)


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentRead:
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return DocumentRead.model_validate(doc)


@router.patch("/{document_id}", response_model=DocumentRead)
async def update_document(
    document_id: uuid.UUID,
    body: DocumentUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentRead:
    """Update metadata on a document. Does not re-trigger ingestion.

    Callers that change tags or folder_id should be aware of two staleness
    limitations, both because the vector store is only written at ingest time:
      1. ``access_keys`` are inherited from the folder at ingest and are NOT
         re-propagated here.
      2. The synthetic ``folder:<id>`` tag that folder-scoped chat/search filter
         on is likewise fixed at ingest. Moving a document to another folder
         does NOT re-tag its vectors, so folder-scoped retrieval will still
         match the OLD folder (and miss the new one) until the document is
         re-ingested. Documents ingested before this feature have no such tag.
    A dedicated metadata/re-tag update task should be dispatched separately
    (see worker.tasks.metadata) to close both gaps. Tracked as a follow-up.
    """
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    fields_set = body.model_fields_set

    if body.title is not None:
        doc.title = body.title
    if "description" in fields_set:
        doc.description = body.description
    if "folder_id" in fields_set:
        doc.folder_id = body.folder_id
    if "metadata" in fields_set:
        doc.metadata_ = body.metadata or {}

    if body.tag_ids is not None:
        if body.tag_ids:
            tag_result = await session.execute(select(Tag).where(Tag.id.in_(body.tag_ids)))
            doc.tags = list(tag_result.scalars().all())
        else:
            doc.tags = []

    await session.flush()
    logger.info("Updated document %s in org %s", document_id, ctx.org_id)
    return DocumentRead.model_validate(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Delete a document. Cascades across PostgreSQL, Qdrant, and Neo4j.

    Cascade to brain-api is best-effort, mirroring delete_org: a transient
    brain-api outage logs an error but does not block the PostgreSQL delete.
    Operators can rerun cleanup from the logs if needed.
    """
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    document_key = doc.document_key
    document_url = doc.document_url
    deleted = await repo.delete(document_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Best-effort cleanup of the stored original ("keep originals" must not leak
    # on delete). A storage outage logs but never blocks the PostgreSQL delete.
    if document_url:
        try:
            StorageClient(settings).delete_object(document_url)
        except Exception as e:
            logger.error(
                "Storage cleanup failed for deleted document %s (key %s) in org %s: %s — manual cleanup needed",
                document_id,
                document_url,
                ctx.org_id,
                e,
            )

    # Best-effort cascade to brain-api. The PostgreSQL delete commits in
    # get_tenant_db on successful return regardless of brain-api outcome.
    try:
        client = BrainAPIClient(settings)
        await client.remove_document(str(ctx.org_id), document_key)
    except Exception as e:
        logger.error(
            "brain-api cleanup failed for deleted document %s (key %s) in org %s: %s — manual cleanup may be required",
            document_id,
            document_key,
            ctx.org_id,
            e,
        )

    logger.info("Deleted document %s in org %s", document_id, ctx.org_id)


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Return indexed chunks for a document by proxying to brain-api."""
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    client = BrainAPIClient(settings)
    try:
        return await client.get_document_chunks(str(ctx.org_id), doc.document_key)
    except Exception as e:
        logger.error("Failed to fetch chunks for %s: %s", doc.document_key, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch chunks from brain-api",
        ) from e


@router.get("/{document_id}/summary")
async def get_document_summary(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Return the hierarchical summary tree for a document by proxying brain-api.

    Mirrors the chunks endpoint's RLS-scoped resolution and auth. A 404 from
    brain-api (the doc-level record isn't written yet because ingestion is
    still running) is surfaced as a 404 so the UI can show a "not available
    yet" state rather than a hard error.
    """
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    client = BrainAPIClient(settings)
    try:
        return await client.get_document_summary(str(ctx.org_id), doc.document_key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Summary not available yet",
            ) from e
        logger.error("Failed to fetch summary for %s: %s", doc.document_key, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch summary from brain-api",
        ) from e
    except Exception as e:
        logger.error("Failed to fetch summary for %s: %s", doc.document_key, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch summary from brain-api",
        ) from e
