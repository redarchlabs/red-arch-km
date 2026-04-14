"""Document ingestion and metadata management endpoints.

Service calls delegate to Qdrant/Neo4j/OpenAI clients which are all blocking
by design (the Python clients for these aren't natively async). We wrap each
call in `asyncio.to_thread` so long blocking I/O runs in the thread pool
instead of stalling the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from brain_api.auth import require_api_key
from brain_api.services.ingest_service import IngestService
from brain_api.stores import Stores, get_stores

logger = logging.getLogger(__name__)
router = APIRouter()


class IngestRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    document_key: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    access_keys: list[int] = Field(default_factory=list)
    use_knowledge_graph: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemoveDocumentRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    document_key: str = Field(min_length=1, max_length=255)


class UpdateMetadataRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    document_key: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=500)
    new_tags: list[str] | None = None
    new_access_keys: list[int] | None = None


class InitTenantRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)


def _get_service(stores: Annotated[Stores, Depends(get_stores)]) -> IngestService:
    return IngestService(stores)


@router.post("/ingest-document")
async def ingest_document(
    body: IngestRequest,
    service: Annotated[IngestService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, Any]:
    """Ingest a document: chunk, embed, summarize, store in vector + graph."""
    try:
        return await asyncio.to_thread(
            service.ingest_document,
            tenant_id=body.tenant_id,
            document_key=body.document_key,
            title=body.title,
            text=body.text,
            tags=body.tags,
            access_keys=body.access_keys,
            use_knowledge_graph=body.use_knowledge_graph,
            metadata=body.metadata,
        )
    except Exception as e:
        logger.exception("Ingestion failed for %s", body.document_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document ingestion failed",
        ) from e


@router.post("/remove-document")
async def remove_document(
    body: RemoveDocumentRequest,
    service: Annotated[IngestService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, str]:
    await asyncio.to_thread(service.remove_document, body.tenant_id, body.document_key)
    return {"status": "deleted", "document_key": body.document_key}


@router.post("/update-document-metadata")
async def update_metadata(
    body: UpdateMetadataRequest,
    service: Annotated[IngestService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, str]:
    await asyncio.to_thread(
        service.update_metadata,
        tenant_id=body.tenant_id,
        document_key=body.document_key,
        tags=body.new_tags,
        access_keys=body.new_access_keys,
        title=body.title,
    )
    return {"status": "updated"}


@router.post("/init-tenant")
async def init_tenant(
    body: InitTenantRequest,
    service: Annotated[IngestService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, str]:
    try:
        await asyncio.to_thread(service.init_tenant, body.tenant_id)
    except Exception as e:
        logger.exception("Tenant init failed for %s", body.tenant_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant initialization failed",
        ) from e
    return {"status": "initialized", "tenant_id": body.tenant_id}


@router.post("/remove-tenant")
async def remove_tenant(
    body: InitTenantRequest,
    service: Annotated[IngestService, Depends(_get_service)],
    _api_key: Annotated[str, Depends(require_api_key)],
) -> dict[str, str]:
    """Delete all Qdrant collections + Neo4j nodes for a tenant.

    Called by the API when an org is deleted. The service layer already
    logs per-store failures and proceeds, so the caller gets 200 even on
    partial failure — operators should monitor brain-api logs to spot
    cleanup gaps. (Failing loudly here would block PostgreSQL deletion
    upstream, which is usually worse than leaving orphan vectors.)
    """
    await asyncio.to_thread(service.remove_tenant, body.tenant_id)
    return {"status": "removed", "tenant_id": body.tenant_id}


@router.get("/documents/{tenant_id}/{document_key}/chunks")
async def get_document_chunks(
    tenant_id: str,
    document_key: str,
    stores: Annotated[Stores, Depends(get_stores)],
    _api_key: Annotated[str, Depends(require_api_key)],
    limit: int = 500,
) -> dict[str, Any]:
    """Return all chunks for a document, ordered by chunk_order."""
    try:
        chunks = await asyncio.to_thread(
            stores.vector.get_document_chunks, tenant_id, document_key, limit=limit
        )
    except Exception:
        logger.exception("Failed to fetch chunks for %s", document_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch chunks",
        )

    return {
        "document_key": document_key,
        "chunks": [
            {
                "id": c.id,
                "text": c.payload.get("text", ""),
                "chunk_order": c.payload.get("chunk_order", 0),
            }
            for c in chunks
        ],
    }
