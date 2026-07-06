"""Document CRUD routes."""

from __future__ import annotations

import io
import logging
import os
import uuid
import zipfile
from typing import Annotated, Any

import httpx
import mammoth
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
from api.schemas.document import (
    DocumentContentUpdate,
    DocumentCreate,
    DocumentRead,
    DocumentUpdate,
    UploadBatchRead,
)
from api.services.brain_client import BrainAPIClient
from api.services.folder_service import compute_folder_masks
from api.services.permission_config import calculate_user_masks_from_membership
from api.services.storage import StorageClient
from api.tasks.ingest import dispatch_extract_ingest, dispatch_ingest, dispatch_metadata_update

logger = logging.getLogger(__name__)
router = APIRouter()

# Extension allowlist for uploads. Extension is authoritative (a mislabelled
# Content-Type must not smuggle in an unsupported type). Kept in sync with the
# worker's extraction dispatcher.
_ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset(
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
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}

_VALID_TRANSLATION_METHODS: frozenset[str] = frozenset({"ocr", "ai"})

# A .zip is expanded server-side into one document per supported member; the
# archive itself is never stored or extracted. Bounds guard against zip bombs
# (a small archive that decompresses to gigabytes) and pathological entry counts.
_ZIP_EXTENSION = ".zip"
_MAX_ZIP_ENTRIES = 200
# Total uncompressed bytes allowed across all members, as a multiple of the
# per-file upload cap. Each member must also individually fit under that cap.
_ZIP_TOTAL_UNCOMPRESSED_FACTOR = 20


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
    folder_repo = FolderRepository(session, ctx.org_id)
    doc_repo = DocumentRepository(session, ctx.org_id)

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
    doc_repo = DocumentRepository(session, ctx.org_id)
    folder_repo = FolderRepository(session, ctx.org_id)

    # Validate folder BEFORE persisting the document, so we reject bad
    # folder_ids (typo / stale client cache / cross-tenant via RLS) with a
    # clean 400 instead of saving a doc that references nothing resolvable.
    access_keys: list[int] = []
    tag_names: list[str] = []
    folder = None
    if body.folder_id is not None:
        folder = await folder_repo.get(body.folder_id)
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id does not exist in this organization",
            )
        access_keys = await folder_repo.effective_view_masks(folder)
        # Encode folder membership as a tag for graph-level filtering
        tag_names.append(f"folder:{folder.id}")

    doc = await doc_repo.create(
        title=body.title,
        text=body.text,
        description=body.description,
        folder_id=body.folder_id,
        uploaded_by_id=ctx.user.profile_id,
        use_knowledge_graph=body.use_knowledge_graph,
        metadata=body.metadata,
        tag_ids=body.tag_ids,
    )

    # Byte-length of pasted text (uploaded files set this from the file size).
    doc.size_bytes = len(body.text.encode("utf-8")) if body.text else None

    # The document is created WITHOUT its own permissions (NULL config): it
    # inherits its folder's entitlement, so a later folder-permission change
    # propagates to it. An admin can override it via the document's Properties,
    # at which point it stops inheriting. The initial ingest below still scopes
    # to the folder's masks (see ``access_keys`` above).

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


def _member_title(name: str) -> str:
    """Title for a zip member: its filename without the extension."""
    return os.path.splitext(name)[0] or name


def _read_zip_members(content: bytes, per_file_max_bytes: int) -> tuple[list[tuple[str, bytes, str]], list[str]]:
    """Expand a ``.zip`` into ``(filename, bytes, content_type)`` per supported member.

    Returns the extracted members plus a list of skipped member names
    (unsupported type, too large, or unreadable). Directories, macOS resource
    forks (``__MACOSX``), and dotfiles are ignored silently. Nested ``.zip``
    members are not recursed into — they are treated as unsupported and skipped.

    Guards against zip bombs: each member must fit under ``per_file_max_bytes``
    and the combined *uncompressed* size is capped. Raises ``HTTPException`` for
    a corrupt archive, too many entries, an oversized payload, or an archive with
    no supported files.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded .zip is not a valid archive",
        ) from e

    total_cap = per_file_max_bytes * _ZIP_TOTAL_UNCOMPRESSED_FACTOR
    members: list[tuple[str, bytes, str]] = []
    skipped: list[str] = []
    total_uncompressed = 0

    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if len(entries) > _MAX_ZIP_ENTRIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The .zip has too many files (limit {_MAX_ZIP_ENTRIES})",
            )

        for info in entries:
            # Flatten paths to their basename; drop resource-fork and hidden junk.
            name = os.path.basename(info.filename)
            if not name or name.startswith(".") or "__MACOSX" in info.filename:
                continue

            member_ext = os.path.splitext(name)[1].lower()
            if member_ext not in _ALLOWED_UPLOAD_EXTENSIONS:
                skipped.append(name)
                continue

            # Reject by the *declared* uncompressed size before decompressing, so
            # a zip bomb can't force us to inflate gigabytes into memory.
            if info.file_size > per_file_max_bytes:
                skipped.append(name)
                continue
            total_uncompressed += info.file_size
            if total_uncompressed > total_cap:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="The .zip contents exceed the total size limit",
                )

            try:
                data = archive.read(info)  # raises on encrypted/corrupt members
            except Exception:
                logger.warning("Skipping unreadable zip member %s", info.filename)
                skipped.append(name)
                continue
            if not data:
                skipped.append(name)
                continue

            content_type = _EXTENSION_CONTENT_TYPES.get(member_ext, "application/octet-stream")
            members.append((name, data, content_type))

    if not members:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The .zip contained no supported files",
        )
    return members, skipped


async def _stage_document(
    *,
    doc_repo: DocumentRepository,
    storage: StorageClient,
    ctx: OrgContext,
    folder: Any | None,
    folder_uuid: uuid.UUID | None,
    filename: str,
    content: bytes,
    content_type: str,
    title: str,
    description: str | None,
    translation_method: str,
    tags: list[str],
    access_keys: list[int],
) -> tuple[Any, dict[str, Any]]:
    """Create a document row, store its original, and build its dispatch payload.

    Does NOT commit or dispatch — the caller commits all staged rows once, then
    enqueues. Stores the object before commit so a storage failure (surfaced as
    a 502) rolls the transaction back rather than leaving a dangling row.
    """
    doc = await doc_repo.create(
        title=title,
        text=None,
        description=description,
        folder_id=folder_uuid,
        uploaded_by_id=ctx.user.profile_id,
    )
    doc.size_bytes = len(content)  # original file size, for the explorer's size column

    # No per-document permissions (NULL config): the document inherits its
    # folder's entitlement so a later folder-permission change propagates to it.
    # Overridable via the document's Properties. The dispatch payload below still
    # scopes to the folder's masks (see ``access_keys``).

    object_key = f"{ctx.org_id}/{doc.document_key}/{filename}"
    try:
        storage.put_object(object_key, content, content_type)
    except Exception as e:
        logger.exception("Failed to store upload for org %s (%s)", ctx.org_id, filename)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store the uploaded file",
        ) from e

    doc.document_url = object_key
    payload = {
        "document_id": str(doc.id),
        "tenant_id": str(ctx.org_id),
        "document_key": doc.document_key,
        "document_url": object_key,
        "filename": filename,
        "title": doc.title,
        "translation_method": translation_method,
        "tags": tags,
        "access_keys": access_keys,
        "use_knowledge_graph": doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True,
        "metadata": doc.metadata_ or {},
    }
    return doc, payload


@router.post("/upload", response_model=None, status_code=status.HTTP_201_CREATED)
async def upload_document(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="The file to ingest (pdf/image/text/zip).")],
    title: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    folder_id: Annotated[str | None, Form()] = None,
    translation_method: Annotated[str, Form()] = "ocr",
) -> DocumentRead | UploadBatchRead:
    """Upload a file, store the original, and enqueue extraction + ingestion.

    Text is extracted in the worker (OCR via Tesseract, or OpenAI vision when
    ``translation_method="ai"``) BEFORE calling brain-api — the brain contract
    stays text-only. The stored object key lives in ``document_url``.

    A ``.zip`` is expanded server-side into one document per supported member
    (unsupported members are skipped); the response is an ``UploadBatchRead``
    instead of a single ``DocumentRead``.
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
    is_zip = ext == _ZIP_EXTENSION
    if not is_zip and ext not in _ALLOWED_UPLOAD_EXTENSIONS:
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
    folder = None
    if folder_id:
        try:
            folder_uuid = uuid.UUID(folder_id)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id is not a valid UUID",
            ) from e
        folder_repo = FolderRepository(session, ctx.org_id)
        folder = await folder_repo.get(folder_uuid)
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id does not exist in this organization",
            )
        access_keys = await folder_repo.effective_view_masks(folder)
        tag_names.append(f"folder:{folder.id}")

    # --- Stage document(s): one for a plain file, N for a .zip's members ---
    doc_repo = DocumentRepository(session, ctx.org_id)
    storage = StorageClient(settings)

    if is_zip:
        members, skipped = _read_zip_members(content, max_bytes)
    else:
        content_type = file.content_type or _EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")
        members = [(filename, content, content_type)]
        skipped = []

    # Store every member BEFORE commit so a storage outage rolls the whole
    # upload back — no dangling PENDING rows pointing at missing objects.
    staged: list[tuple[Any, dict[str, Any]]] = []
    for member_name, member_bytes, member_type in members:
        doc, payload = await _stage_document(
            doc_repo=doc_repo,
            storage=storage,
            ctx=ctx,
            folder=folder,
            folder_uuid=folder_uuid,
            filename=member_name,
            content=member_bytes,
            content_type=member_type,
            title=_member_title(member_name) if is_zip else title,
            description=description,
            translation_method=translation_method,
            tags=tag_names,
            access_keys=access_keys,
        )
        staged.append((doc, payload))

    await session.commit()

    # Enqueue extraction per document. Mirror create_document: a broker outage
    # must not turn a successful upload into a 500 — the rows + objects exist, so
    # leave any un-enqueued doc PENDING for retry rather than failing the request.
    for doc, payload in staged:
        try:
            task_id = dispatch_extract_ingest(payload)
            logger.info("Upload %s queued for extraction+ingestion (task %s)", doc.id, task_id)
        except Exception:
            logger.exception(
                "Upload %s stored but extraction enqueue failed; left PENDING for retry",
                doc.id,
            )

    if is_zip:
        return UploadBatchRead(
            created=len(staged),
            skipped=skipped,
            documents=[DocumentRead.model_validate(doc) for doc, _ in staged],
        )
    return DocumentRead.model_validate(staged[0][0])


@router.get("/by-key/{document_key}", response_model=DocumentRead)
async def get_document_by_key(
    document_key: str,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentRead:
    """Resolve a document by its ``document_key``.

    Chat/search sources reference documents by the vector-store key (not the
    Postgres id), so the UI resolves that key here to the canonical document
    before navigating. Declared BEFORE ``/{document_id}`` so the literal
    ``by-key`` prefix isn't swallowed by the id path.
    """
    repo = DocumentRepository(session, ctx.org_id)
    doc = await repo.get_by_key(document_key)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return DocumentRead.model_validate(doc)


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DocumentRead:
    repo = DocumentRepository(session, ctx.org_id)
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
    """Update metadata on a document. Does not re-chunk/re-embed.

    Changing ``folder_id``, ``tag_ids``, or ``title`` re-propagates the derived
    vector-store metadata (the synthetic ``folder:<id>`` tag + the folder's
    ``access_keys`` + the title) to the document's existing vectors via a
    metadata-update task — so folder-scoped chat/search and entitlement
    filtering stay correct after a move, without a full re-ingest. (Documents
    ingested before the folder-tag feature only gain the tag once touched here
    or re-ingested.)
    """
    repo = DocumentRepository(session, ctx.org_id)
    folder_repo = FolderRepository(session, ctx.org_id)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    fields_set = body.model_fields_set

    if body.title is not None:
        doc.title = body.title
    if "description" in fields_set:
        doc.description = body.description
    if "folder_id" in fields_set:
        # Validate the target folder so a bad id is rejected cleanly (mirrors
        # create_document) rather than silently detaching the document.
        if body.folder_id is not None and await folder_repo.get(body.folder_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="folder_id does not exist in this organization",
            )
        doc.folder_id = body.folder_id
    if "metadata" in fields_set:
        doc.metadata_ = body.metadata or {}

    if body.tag_ids is not None:
        if body.tag_ids:
            tag_result = await session.execute(select(Tag).where(Tag.id.in_(body.tag_ids)))
            doc.tags = list(tag_result.scalars().all())
        else:
            doc.tags = []

    # Per-document permissions: setting either config recomputes this document's
    # own masks (resolved together so masks reflect the current stored configs).
    perms_changed = bool({"viewer_permissions_config", "contributor_permissions_config"} & fields_set)
    if "viewer_permissions_config" in fields_set:
        doc.viewer_permissions_config = body.viewer_permissions_config
    if "contributor_permissions_config" in fields_set:
        doc.contributor_permissions_config = body.contributor_permissions_config
    if perms_changed:
        view_masks, contrib_masks = await compute_folder_masks(
            session,
            ctx.org_id,
            doc.viewer_permissions_config,
            doc.contributor_permissions_config,
        )
        doc.view_permission_masks = view_masks
        doc.contributor_permission_masks = contrib_masks

    await session.flush()

    # If anything that shapes the vector-store payload changed, recompute the
    # derived tags + access_keys and push them to brain-api so retrieval scoping
    # and entitlement filtering aren't stale after a move or permission change.
    retag = bool({"folder_id", "tag_ids"} & fields_set) or body.title is not None or perms_changed
    if retag:
        access_keys: list[int] = []
        tag_names = [t.name for t in doc.tags]
        folder = await folder_repo.get(doc.folder_id) if doc.folder_id is not None else None
        if folder is not None:
            tag_names.append(f"folder:{doc.folder_id}")
        # The document's OWN viewer permissions take precedence for entitlement;
        # otherwise it inherits its folder's effective masks (which themselves
        # inherit up the folder tree when the folder has no config of its own).
        if doc.viewer_permissions_config is not None:
            access_keys = list(doc.view_permission_masks or [])
        else:
            access_keys = await folder_repo.effective_view_masks(folder)
        await session.commit()
        try:
            dispatch_metadata_update(
                {
                    "tenant_id": str(ctx.org_id),
                    "document_key": doc.document_key,
                    "new_tags": tag_names,
                    "new_access_keys": access_keys,
                    "title": doc.title,
                }
            )
        except Exception:
            # A broker outage must not fail the (already-committed) update; the
            # vector payload is just briefly stale until the next touch.
            logger.exception("Metadata re-tag enqueue failed for document %s", doc.id)

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
    repo = DocumentRepository(session, ctx.org_id)
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


# Text-based originals we can return verbatim for formatted reading.
_TEXT_ORIGINAL_EXTENSIONS: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}
# Binary originals the browser renders natively — served via a signed URL and
# embedded in the reader beside the summary tree.
_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"})


@router.get("/{document_id}/content")
async def get_document_content(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Describe how to display a document readably (original, not index chunks).

    The indexed chunks are whitespace-flattened for embedding quality, so they
    read as a wall of text. Here we return the source instead. The response tells
    the reader which strategy to use via ``kind``:
      - "markdown"/"text": ``content`` holds the original text to render.
      - "pdf"/"image": ``original_url`` is a short-lived signed URL to the
        original file, embedded natively (perfect formatting).
      - "other": nothing readable here → the reader falls back to chunks.
    """
    repo = DocumentRepository(session, ctx.org_id)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    empty = {"content": None, "format": None, "kind": "other", "original_url": None}

    # Pasted/authored text (kept verbatim; the editor emits Markdown).
    if doc.text:
        return {"content": doc.text, "format": "markdown", "kind": "markdown", "original_url": None}

    if not doc.document_url:
        return empty

    ext = os.path.splitext(doc.document_url)[1].lower()

    # Text originals (.md/.txt) — read back and render as text.
    fmt = _TEXT_ORIGINAL_EXTENSIONS.get(ext)
    if fmt is not None:
        try:
            raw = StorageClient(settings).get_object(doc.document_url)
            return {
                "content": raw.decode("utf-8", errors="replace"),
                "format": fmt,
                "kind": fmt,
                "original_url": None,
            }
        except Exception:
            logger.exception("Failed to read original for %s", doc.document_key)
            return empty

    # Legacy .doc — the worker stored a text sidecar at ingest (antiword only
    # runs there); serve that as plain text.
    if ext == ".doc":
        try:
            raw = StorageClient(settings).get_object(f"{doc.document_url}.extracted.txt")
            return {
                "content": raw.decode("utf-8", errors="replace"),
                "format": "text",
                "kind": "text",
                "original_url": None,
            }
        except Exception:
            logger.info("No .doc text sidecar for %s (may still be processing)", doc.document_key)
            return empty

    # Word documents — convert to Markdown so they render with their structure.
    if ext == ".docx":
        try:
            raw = StorageClient(settings).get_object(doc.document_url)
            markdown = mammoth.convert_to_markdown(io.BytesIO(raw)).value
            return {"content": markdown, "format": "markdown", "kind": "markdown", "original_url": None}
        except Exception:
            logger.exception("Failed to convert docx original for %s", doc.document_key)
            return empty

    # Binary originals the browser renders natively — hand back a signed URL.
    if ext == ".pdf" or ext in _IMAGE_EXTENSIONS:
        kind = "pdf" if ext == ".pdf" else "image"
        content_type = _EXTENSION_CONTENT_TYPES.get(ext, "application/octet-stream")
        try:
            url = StorageClient(settings).presigned_get_url(doc.document_url, content_type=content_type)
            return {"content": None, "format": None, "kind": kind, "original_url": url}
        except Exception:
            logger.exception("Failed to sign original URL for %s", doc.document_key)
            return empty

    return empty


async def _purge_brain_vectors(settings: Settings, tenant_id: str, document_key: str) -> None:
    """Best-effort purge of a document's existing vectors/graph before re-ingest.

    Re-ingestion is NOT idempotent on the brain-api side: it inserts a fresh set
    of points with new random ids and never deletes the previous set. So an edit
    that re-dispatched ingestion without purging first would accumulate duplicate
    chunks on every save. Removing by ``document_key`` first (the same purge the
    delete path uses) makes a re-ingest replace rather than append. Best-effort:
    a brain-api outage logs but never fails the already-committed save.
    """
    try:
        await BrainAPIClient(settings).remove_document(tenant_id, document_key)
    except Exception:
        logger.warning(
            "brain-api purge before re-ingest failed for %s; duplicate chunks may result",
            document_key,
        )


async def _derive_ingest_scoping(folder_repo: FolderRepository, doc: Any) -> tuple[list[str], list[int]]:
    """Rebuild a document's vector-store scoping for a re-ingest.

    Returns the synthetic ``folder:<id>`` tag list and the entitlement
    ``access_keys``. Mirrors the derivation in ``create_document`` /
    ``update_document`` so re-indexing keeps folder-scoped retrieval and
    permission filtering correct: the document's OWN viewer permissions take
    precedence, otherwise it inherits its folder's effective masks (which
    themselves inherit up the folder tree when unconfigured).
    """
    tag_names: list[str] = []
    access_keys: list[int] = []
    folder = await folder_repo.get(doc.folder_id) if doc.folder_id is not None else None
    if folder is not None:
        tag_names.append(f"folder:{doc.folder_id}")
    if doc.viewer_permissions_config is not None:
        access_keys = list(doc.view_permission_masks or [])
    else:
        access_keys = await folder_repo.effective_view_masks(folder)
    return tag_names, access_keys


@router.put("/{document_id}/content", response_model=DocumentRead)
async def update_document_content(
    document_id: uuid.UUID,
    body: DocumentContentUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentRead:
    """Replace a document's body text and re-index it.

    Unlike ``PATCH`` (metadata only), this re-chunks and re-embeds. It handles
    both authoring paths uniformly:
      - Authored inline text (stored in ``Document.text``) is rewritten in the
        row and re-ingested via the text-only path.
      - Uploaded text originals (``.md``/``.markdown``/``.txt`` in object
        storage) are rewritten in place and re-extracted.
    Non-text originals (PDF, images, Word) have no editable text body and are
    rejected with 415.

    The row is committed before the (synchronous) broker enqueue, so a broker
    outage leaves the saved content PENDING for a later sweep rather than
    turning a successful save into a 500.
    """
    repo = DocumentRepository(session, ctx.org_id)
    folder_repo = FolderRepository(session, ctx.org_id)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    text = body.text
    tag_names, access_keys = await _derive_ingest_scoping(folder_repo, doc)
    use_kg = doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True

    # Storage-backed original: rewrite the object in place. Only text originals
    # are editable as text — a PDF/image/Word body isn't.
    if doc.document_url:
        ext = os.path.splitext(doc.document_url)[1].lower()
        if ext not in _TEXT_ORIGINAL_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="This document type can't be edited as text.",
            )
        content = text.encode("utf-8")
        content_type = _EXTENSION_CONTENT_TYPES.get(ext, "text/plain")
        try:
            StorageClient(settings).put_object(doc.document_url, content, content_type)
        except Exception as e:
            logger.exception("Failed to store edited content for %s", doc.document_key)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to store the edited document",
            ) from e
        doc.size_bytes = len(content)
        doc.processing_status = "PENDING"
        await session.commit()
        await _purge_brain_vectors(settings, str(ctx.org_id), doc.document_key)
        try:
            dispatch_extract_ingest(
                {
                    "document_id": str(doc.id),
                    "tenant_id": str(ctx.org_id),
                    "document_key": doc.document_key,
                    "document_url": doc.document_url,
                    "filename": os.path.basename(doc.document_url),
                    "title": doc.title,
                    "translation_method": "ocr",  # ignored for text originals
                    "tags": tag_names,
                    "access_keys": access_keys,
                    "use_knowledge_graph": use_kg,
                    "metadata": doc.metadata_ or {},
                }
            )
        except Exception:
            logger.exception("Content saved for %s but re-ingest enqueue failed; left PENDING", doc.id)
        return DocumentRead.model_validate(doc)

    # Inline authored text: store verbatim in the row and re-ingest via the
    # text-only path. Clearing to empty persists but skips ingestion.
    doc.text = text or None
    if doc.text:
        doc.size_bytes = len(text.encode("utf-8"))
        doc.processing_status = "PENDING"
        await session.commit()
        await _purge_brain_vectors(settings, str(ctx.org_id), doc.document_key)
        try:
            dispatch_ingest(
                {
                    "document_id": str(doc.id),
                    "tenant_id": str(ctx.org_id),
                    "document_key": doc.document_key,
                    "title": doc.title,
                    "text": doc.text,
                    "tags": tag_names,
                    "access_keys": access_keys,
                    "use_knowledge_graph": use_kg,
                    "metadata": doc.metadata_ or {},
                }
            )
        except Exception:
            logger.exception("Content saved for %s but re-ingest enqueue failed; left PENDING", doc.id)
    else:
        doc.size_bytes = None
        await session.commit()
        # Clearing the body should also drop the now-stale vectors.
        await _purge_brain_vectors(settings, str(ctx.org_id), doc.document_key)
        logger.info("Document %s content cleared; skipping re-ingest", doc.id)

    return DocumentRead.model_validate(doc)


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    document_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Return one page of indexed chunks for a document by proxying to brain-api.

    Paginated (offset/limit) so a large document can be lazy-loaded a page at a
    time by the reader rather than fetching every chunk up front.
    """
    repo = DocumentRepository(session, ctx.org_id)
    doc = await repo.get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    client = BrainAPIClient(settings)
    try:
        return await client.get_document_chunks(str(ctx.org_id), doc.document_key, offset=offset, limit=limit)
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
    repo = DocumentRepository(session, ctx.org_id)
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
