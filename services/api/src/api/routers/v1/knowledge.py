"""``/api/v1/knowledge`` — read the knowledge base: folders, documents, content.

Read-only in this release (``knowledge:read``): list folders + documents and pull
a document's extracted chunks/summary from brain-api. Ingesting new documents
(``knowledge:write``) is reserved for a later pass. An org service key sees all of
the org's folders/documents (org-wide access).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.config import Settings, get_settings
from api.models.document import Document
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.document import DocumentRead, FolderRead
from api.services.brain_client import BrainAPIClient

router = APIRouter()


@router.get("/folders", response_model=list[FolderRead])
async def list_folders(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> list[FolderRead]:
    """List all folders in the org."""
    folders, _total = await FolderRepository(session, principal.org_id).list_visible_to_masks(user_masks=None)
    return [FolderRead.model_validate(f) for f in folders]


@router.get("/documents", response_model=PaginatedResponse[DocumentRead])
async def list_documents(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("knowledge:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
    folder_id: Annotated[uuid.UUID | None, Query()] = None,
) -> PaginatedResponse[DocumentRead]:
    """List documents, optionally scoped to a single folder."""
    folder_ids = [folder_id] if folder_id is not None else None
    docs, total = await DocumentRepository(session, principal.org_id).list_for_folders(
        folder_ids=folder_ids,
        include_unfiled=folder_ids is None,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return make_page([DocumentRead.model_validate(d) for d in docs], total, pagination)


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
    """Resolve an org-scoped document by id or raise 404."""
    doc = await DocumentRepository(session, principal.org_id).get(document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return doc
