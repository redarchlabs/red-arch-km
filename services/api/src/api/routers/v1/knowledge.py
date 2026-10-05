"""``/api/v1/knowledge`` — the knowledge base: folders, documents, content, writes.

Reads (``knowledge:read``) list folders + documents and pull a document's
extracted chunks/summary from brain-api. ``POST /documents`` (``knowledge:write``)
adds a document, or a new version of one, through the same ingest pipeline the
first-party UI uses (see :mod:`api.services.knowledge_write`).

What a key sees depends on its access mode (``api_key_access_keys``):

* an **org key** sees all of the org's folders and documents;
* a **scoped key** sees what its own dimension assignments may see
  (:mod:`api.services.knowledge_visibility`), and — when it lists folders — only
  inside those folders and their subfolders (``ApiKeyPrincipal.folder_ids``). A
  folder or document it may not see is a ``404`` — and for chunks/summary,
  brain-api is never asked.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.types import Message

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.config import Settings, get_settings
from api.dependencies import get_redis
from api.models.document import Document
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.document import DocumentRead, FolderRead
from api.schemas.knowledge_write import (
    KnowledgeDocumentFormFields,
    KnowledgeDocumentJsonWrite,
    KnowledgeDocumentWriteResult,
)
from api.services.api_key_write_cap import DOC_WRITE_WINDOW_SECONDS, doc_write_cap_key
from api.services.api_rate_limit import check_rate_limit, peek_rate_limit
from api.services.brain_client import BrainAPIClient
from api.services.document_ingest import ALLOWED_UPLOAD_EXTENSIONS, read_upload_bounded
from api.services.knowledge_visibility import document_visible, visible_folder_ids
from api.services.knowledge_write import KnowledgeDocumentInput, KnowledgeWriteError, KnowledgeWriteService
from api.services.search_access import api_key_access_keys

router = APIRouter()

_MULTIPART = "multipart/form-data"
_JSON = "application/json"
_FORM_FIELDS = frozenset({"folder_id", "title", "external_ref", "metadata"})
# Headroom over the content limit for the JSON envelope (field names, escaping,
# metadata) before a body is rejected unparsed; the content itself is then
# checked against the exact limit.
_JSON_ENVELOPE_ALLOWANCE = 64 * 1024
# Same headroom for the multipart envelope (boundaries, part headers, form fields).
_MULTIPART_ENVELOPE_ALLOWANCE = 64 * 1024
# Filenames become object-store keys: a conservative alphabet and length.
_FILENAME_MAX = 200
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


@router.get("/folders", response_model=list[FolderRead])
async def list_folders(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[FolderRead]:
    """List the folders this key may see (all of them for an org key)."""
    masks = api_key_access_keys(principal)
    repo = FolderRepository(session, principal.org_id)
    if principal.folder_ids is not None:
        # Already the listed subtrees capped by the masks (resolved per request).
        folders = await repo.get_many(principal.folder_ids)
    else:
        folders, _total = await repo.list_visible_to_masks(user_masks=masks)
    return [FolderRead.model_validate(f) for f in folders]


@router.get("/documents", response_model=PaginatedResponse[DocumentRead])
async def list_documents(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
    folder_id: Annotated[uuid.UUID | None, Query()] = None,
) -> PaginatedResponse[DocumentRead]:
    """List documents, optionally scoped to a single folder."""
    masks = api_key_access_keys(principal)
    repo = DocumentRepository(session, principal.org_id)
    if masks is None:
        folder_ids = [folder_id] if folder_id is not None else None
        docs, total = await repo.list_for_folders(
            folder_ids=folder_ids,
            include_unfiled=folder_ids is None,
            offset=pagination.offset,
            limit=pagination.page_size,
        )
    else:
        # A folder-limited key's set is already capped by its masks (resolved per
        # request); only a key without folder limits needs the org-wide pass.
        visible = (
            sorted(principal.folder_ids, key=str)
            if principal.folder_ids is not None
            else await visible_folder_ids(session, principal.org_id, masks)
        )
        if folder_id is not None and folder_id not in visible:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder not found")
        if not visible:
            return make_page([], 0, pagination)
        docs, total = await repo.list_for_folders(
            folder_ids=[folder_id] if folder_id is not None else visible,
            include_unfiled=False,  # unfiled documents bypass folder permissions
            offset=pagination.offset,
            limit=pagination.page_size,
            viewer_masks=masks,
        )
    return make_page([DocumentRead.model_validate(d) for d in docs], total, pagination)


@router.post(
    "/documents",
    response_model=KnowledgeDocumentWriteResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "A new version, a metadata change, a retry, or a no-op for an existing external_ref"},
        403: {"description": "Missing knowledge:write, or the key's assignments may not add to this folder"},
        404: {"description": "Folder not found (or not visible to / outside the folders of a scoped key)"},
        409: {"description": "external_ref conflict, or the previous version is still processing"},
        413: {"description": "Content over the upload size limit"},
        415: {"description": "Body is neither application/json nor multipart/form-data"},
        429: {"description": "Over this key's daily document-write cap"},
        503: {"description": "Index or ingest queue unavailable; resend to retry"},
    },
)
async def write_document(
    request: Request,
    response: Response,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> KnowledgeDocumentWriteResult:
    """Add a document to a folder, or replace one by ``external_ref``.

    Send **either** ``application/json`` — ``{folder_id, title, content,
    external_ref?, metadata?}`` with text content — **or** ``multipart/form-data``
    with a ``file`` part plus ``folder_id`` and optional ``title`` (defaults to
    the filename), ``external_ref`` and ``metadata`` (a JSON object string).

    Ingest is asynchronous: the response carries the document id and its
    processing status; poll ``GET /api/v1/knowledge/documents/{id}``. ``201`` for
    a new document; ``200`` with ``outcome`` ``updated`` / ``reprocessed`` /
    ``metadata_updated`` / ``unchanged`` when the ``external_ref`` already exists
    in that folder. Size and type limits match the first-party upload
    (``MAX_FILE_SIZE_MB``; pdf, images, txt, md, docx, doc — one file per call, no
    .zip). Each key may write ``API_KEY_DOCUMENT_WRITES_PER_DAY`` documents per day.
    """
    # Refuse an exhausted key before reading the body; count only real writes below.
    await _refuse_if_daily_cap_spent(redis, principal, settings)
    data = await _parse_write(request, settings)
    service = KnowledgeWriteService(session, principal, settings)
    try:
        result = await service.upsert(data)
    except KnowledgeWriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if result.outcome != "unchanged":
        # Counted only once the write was authorised and actually did something.
        await check_rate_limit(
            redis,
            _daily_cap_key(principal),
            limit=settings.api_key_document_writes_per_day,
            window_seconds=DOC_WRITE_WINDOW_SECONDS,
        )
    if result.outcome != "created":
        response.status_code = status.HTTP_200_OK
    doc = result.document
    return KnowledgeDocumentWriteResult(
        id=doc.id,
        document_key=doc.document_key,
        external_ref=doc.external_ref,
        folder_id=doc.folder_id,
        title=doc.title,
        processing_status=doc.processing_status,
        content_hash=doc.content_hash,
        outcome=result.outcome,
    )


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> DocumentRead:
    """Fetch a single document's metadata + ingest status."""
    return DocumentRead.model_validate(await _load_document(session, principal, document_id))


@router.get("/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Fetch a page of a document's extracted text chunks from brain-api."""
    doc = await _load_document(session, principal, document_id)
    client = BrainAPIClient(settings)
    return await client.get_document_chunks(str(principal.org_id), doc.document_key, offset=offset, limit=limit)


@router.get("/documents/{document_id}/summary")
async def get_document_summary(
    document_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Fetch a document's summary (and hierarchical summary tree) from brain-api."""
    doc = await _load_document(session, principal, document_id)
    client = BrainAPIClient(settings)
    return await client.get_document_summary(str(principal.org_id), doc.document_key)


async def _load_document(session: AsyncSession, principal: ApiKeyPrincipal, document_id: uuid.UUID) -> Document:
    """Resolve a document this key may see, or raise 404.

    brain-api's chunk/summary endpoints do not filter by masks, so this check is
    the gate for them: a document a scoped key cannot see must never reach
    brain-api at all.
    """
    doc = await DocumentRepository(session, principal.org_id).get(document_id)
    masks = api_key_access_keys(principal)
    outside_folders = principal.folder_ids is not None and (doc is None or doc.folder_id not in principal.folder_ids)
    if doc is None or outside_folders or not await document_visible(session, principal.org_id, doc, masks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return doc


# ---- write: boundary parsing ---------------------------------------------------


def _unprocessable(exc: ValidationError) -> RequestValidationError:
    return RequestValidationError(exc.errors(include_url=False, include_context=False))


def _check_size(size: int, settings: Settings) -> None:
    if size > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Content exceeds the {settings.max_file_size_mb} MB limit",
        )


async def _parse_write(request: Request, settings: Settings) -> KnowledgeDocumentInput:
    """Validate either body form into one :class:`KnowledgeDocumentInput`."""
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type == _JSON:
        return await _parse_json(request, settings)
    if content_type == _MULTIPART:
        return await _parse_multipart(request, settings)
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Send application/json (text content) or multipart/form-data (a file)",
    )


async def _read_json_body_bounded(request: Request, settings: Settings) -> bytes:
    """Stream the body, aborting with 413 once it is clearly over the limit, so an
    oversized JSON document is never buffered whole. The envelope allowance covers
    the field names, escaping and metadata around the content."""
    cap = settings.max_file_size_mb * 1024 * 1024 + _JSON_ENVELOPE_ALLOWANCE
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            _check_size(total, settings)
        chunks.append(chunk)
    return b"".join(chunks)


async def _parse_json(request: Request, settings: Settings) -> KnowledgeDocumentInput:
    raw = await _read_json_body_bounded(request, settings)
    try:
        body = KnowledgeDocumentJsonWrite.model_validate_json(raw)
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    content = body.content.encode("utf-8")
    _check_size(len(content), settings)
    return KnowledgeDocumentInput(
        folder_id=body.folder_id,
        title=body.title,
        content=content,
        external_ref=body.external_ref,
        metadata=body.metadata,
    )


def _declared_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _bounded_request(request: Request, cap: int, settings: Settings) -> Request:
    """A view of ``request`` whose body stream raises 413 past ``cap`` bytes.

    Starlette spools a multipart file part to disk before the handler sees it, so
    checking the size afterwards lets a caller fill the disk first. Counting the
    raw body as it is received stops it as soon as the cap is crossed, whether or
    not the client declared a Content-Length (chunked uploads do not).
    """
    received = 0
    upstream = request.receive

    async def receive() -> Message:
        nonlocal received
        message = await upstream()
        if message.get("type") == "http.request":
            received += len(message.get("body", b""))
            if received > cap:
                _check_size(received, settings)
        return message

    return Request(request.scope, receive)


async def _parse_multipart(request: Request, settings: Settings) -> KnowledgeDocumentInput:
    cap = settings.max_file_size_mb * 1024 * 1024 + _MULTIPART_ENVELOPE_ALLOWANCE
    declared = _declared_length(request)
    if declared is not None and declared > cap:
        _check_size(declared, settings)  # refuse before reading a byte
    bounded = _bounded_request(request, cap, settings)
    form = await bounded.form(max_files=1, max_fields=len(_FORM_FIELDS) + 1)
    try:
        return await _form_to_input(form, settings)
    finally:
        await form.close()  # release the spooled upload


def _safe_filename(raw: str) -> str:
    """Basename only, a conservative alphabet, no leading dots, at most 200 chars
    with the extension kept. Empty when nothing usable remains."""
    name = _FILENAME_UNSAFE.sub("_", os.path.basename(raw.replace("\\", "/"))).lstrip(". ").strip()
    if len(name) > _FILENAME_MAX:
        stem, ext = os.path.splitext(name)
        ext = ext[:16]
        name = stem[: _FILENAME_MAX - len(ext)] + ext
    return name


def _daily_cap_key(principal: ApiKeyPrincipal) -> str:
    # Shared with the create_document tool of runs this key starts.
    return doc_write_cap_key(principal.api_key_id)


async def _refuse_if_daily_cap_spent(redis: Redis, principal: ApiKeyPrincipal, settings: Settings) -> None:
    """Every write may start an LLM-billed ingest, so each key has a daily cap.

    Checked here without counting, before the body is read; the handler counts a
    write only after it was authorised and changed something (a refused or
    ``unchanged`` request is free). Fail-open on a Redis outage, like the
    per-minute limiter. Concurrent writes at the boundary can overshoot by the
    number in flight.
    """
    result = await peek_rate_limit(
        redis,
        _daily_cap_key(principal),
        limit=settings.api_key_document_writes_per_day,
        window_seconds=DOC_WRITE_WINDOW_SECONDS,
    )
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This API key has reached its daily document-write limit",
            headers={"Retry-After": str(result.retry_after)},
        )


async def _form_to_input(form: FormData, settings: Settings) -> KnowledgeDocumentInput:
    upload = form.get("file")
    fields = {k: v for k, v in form.items() if k != "file"}
    unknown = set(fields) - _FORM_FIELDS
    if unknown:
        raise RequestValidationError(
            [{"type": "extra_forbidden", "loc": ("body", name), "msg": "Unexpected field"} for name in sorted(unknown)]
        )
    if not isinstance(upload, StarletteUploadFile):
        raise RequestValidationError([{"type": "missing", "loc": ("body", "file"), "msg": "A file part is required"}])
    try:
        meta = KnowledgeDocumentFormFields.model_validate(fields)
    except ValidationError as exc:
        raise _unprocessable(exc) from exc

    filename = _safe_filename(upload.filename or "")
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A filename is required")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext or filename}'. Allowed: {sorted(ALLOWED_UPLOAD_EXTENSIONS)}",
        )
    content = await read_upload_bounded(_as_upload(upload), settings.max_file_size_mb)
    return KnowledgeDocumentInput(
        folder_id=meta.folder_id,
        title=meta.title or (os.path.splitext(filename)[0] or filename),
        content=content,
        filename=filename,
        content_type=upload.content_type,
        external_ref=meta.external_ref,
        metadata=meta.metadata,
    )


def _as_upload(upload: StarletteUploadFile) -> UploadFile:
    # FastAPI's UploadFile subclasses Starlette's; read_upload_bounded only reads.
    return upload  # type: ignore[return-value]
