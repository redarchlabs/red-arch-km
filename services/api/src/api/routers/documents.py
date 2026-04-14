"""Document CRUD routes."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
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
from api.tasks.ingest import dispatch_ingest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=PaginatedResponse[DocumentRead])
async def list_documents(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[DocumentRead]:
    """List documents the user can view through folder permissions."""
    folder_repo = FolderRepository(session)
    doc_repo = DocumentRepository(session)

    if ctx.is_org_admin:
        visible_folders = await folder_repo.list_visible_to_masks(user_masks=None)
    else:
        org = await session.get(Org, ctx.org_id)
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        user_masks = calculate_user_masks_from_membership(ctx.membership, org.permission_number)
        visible_folders = await folder_repo.list_visible_to_masks(user_masks=user_masks)

    folder_ids = [f.id for f in visible_folders]
    documents, total = await doc_repo.list_for_folders(
        folder_ids=folder_ids,
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

    # Inherit access masks from the folder (if any)
    access_keys: list[int] = []
    tag_names: list[str] = []
    if body.folder_id is not None:
        folder = await folder_repo.get(body.folder_id)
        if folder is not None:
            access_keys = list(folder.view_permission_masks or [])
            # Encode folder membership as a tag for graph-level filtering
            tag_names.append(f"folder:{folder.id}")

    if doc.text:
        task_id = dispatch_ingest({
            "tenant_id": str(ctx.org_id),
            "document_key": doc.document_key,
            "title": doc.title,
            "text": doc.text,
            "tags": tag_names,
            "access_keys": access_keys,
            "use_knowledge_graph": doc.use_knowledge_graph if doc.use_knowledge_graph is not None else True,
            "metadata": doc.metadata_ or {},
        })
        logger.info("Document %s queued for ingestion (task %s)", doc.id, task_id)
    else:
        logger.info("Document %s created without text; skipping ingestion", doc.id)

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

    Callers that change tags or folder_id should be aware: the vector-store
    access_keys are inherited from the folder at ingest time and are NOT
    automatically re-propagated here. A dedicated metadata update task
    should be dispatched separately (see worker.tasks.metadata).
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
) -> None:
    repo = DocumentRepository(session)
    deleted = await repo.delete(document_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
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
