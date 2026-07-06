"""Internal service-to-service endpoints.

These endpoints are NOT exposed to end-users. They authenticate via a
shared secret (X-Internal-API-Key) separate from user JWTs and from the
brain-api key, and are used for callbacks from trusted workers — e.g.
the Celery ingest worker reporting document processing status back to
the API service.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import require_internal_api_key
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.models.document import ProcessingStatus
from api.models.org import Org
from api.repositories.document import DocumentRepository

logger = logging.getLogger(__name__)
router = APIRouter()


class OrgOpenAIKey(BaseModel):
    """Per-org OpenAI key lookup result for the worker."""

    openai_api_key: str | None = Field(
        default=None,
        description="The org's OpenAI key, or null to fall back to the central key.",
    )


class DocumentStatusUpdate(BaseModel):
    """Worker-reported processing status for a document."""

    tenant_id: uuid.UUID = Field(description="Org ID, used to set RLS scope.")
    status: ProcessingStatus = Field(
        description="One of PENDING, PROCESSING, SUCCESS, FAILED.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured detail blob (chunks/triplets/error).",
    )


@router.post(
    "/documents/{document_id}/status",
    dependencies=[Depends(require_internal_api_key)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_document_status(
    document_id: uuid.UUID,
    body: DocumentStatusUpdate,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Set the processing_status + processing_details for a document.

    Called by the worker after ingestion completes (success or permanent
    failure). Uses a dedicated session so we can set the RLS tenant
    context before the UPDATE — the worker has no user JWT, so we can't
    go through the normal `get_tenant_db` path.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await _set_tenant(session, body.tenant_id)
            repo = DocumentRepository(session, body.tenant_id)
            doc = await repo.update_status(document_id, status=body.status, details=body.details)
            if doc is None:
                # RLS filtered it out, or it was deleted while processing.
                # Either way this is a no-op — don't 500 the worker.
                logger.warning(
                    "Status callback for unknown document %s (tenant %s) — ignored",
                    document_id,
                    body.tenant_id,
                )
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found (may have been deleted)",
                )
            await session.commit()
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            logger.exception("Failed to update status for document %s", document_id)
            raise


@router.get(
    "/orgs/{org_id}/openai-key",
    dependencies=[Depends(require_internal_api_key)],
    response_model=OrgOpenAIKey,
)
async def get_org_openai_key(
    org_id: uuid.UUID,
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrgOpenAIKey:
    """Return the per-org OpenAI key so the worker can use it for AI OCR.

    The worker calls this (not the Celery payload) so the secret never rides
    the broker. A null key tells the worker to fall back to the central
    OPENAI_API_KEY. Uses a dedicated RLS-scoped session, mirroring the status
    callback — the worker has no user JWT.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        await _set_tenant(session, org_id)
        org = await session.get(Org, org_id)
        # RLS should already scope to this org; the id check is belt-and-braces.
        if org is None or org.id != org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Org not found",
            )
        return OrgOpenAIKey(openai_api_key=org.openai_api_key)


class DispatchResult(BaseModel):
    """Counters returned by a workflow dispatch sweep."""

    events: int = 0
    runs: int = 0
    actions: int = 0
    skipped: int = 0


@router.post(
    "/workflows/dispatch-batch",
    dependencies=[Depends(require_internal_api_key)],
    response_model=DispatchResult,
)
async def dispatch_workflows(
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int = 100,
) -> DispatchResult:
    """Claim and process a batch of pending workflow-outbox events.

    Runs on the privileged session (no tenant context) so the cross-org claim
    sees every tenant; the dispatcher scopes each event's work by org_id.
    """
    from api.services.workflow.dispatcher import WorkflowDispatchService

    allowlist = tuple(getattr(settings, "workflow_webhook_allowlist", ()) or ())
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            service = WorkflowDispatchService(session, webhook_allowlist=allowlist)
            counters = await service.process_pending(limit=limit)
            await session.commit()
            return DispatchResult(**{k: counters.get(k, 0) for k in ("events", "runs", "actions", "skipped")})
        except Exception:
            await session.rollback()
            logger.exception("workflow dispatch batch failed")
            raise


@router.post(
    "/workflows/maintain-partitions",
    dependencies=[Depends(require_internal_api_key)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def maintain_partitions(
    settings: Annotated[Settings, Depends(get_settings)],
    months_ahead: Annotated[int, Query(ge=0, le=24)] = 2,
) -> None:
    """Pre-create upcoming month partitions for the workflow tables."""
    factory = get_session_factory(settings)
    async with factory() as session:
        await session.execute(text("SELECT workflow_ensure_partitions(:m)"), {"m": months_ahead})
        await session.commit()


async def _set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Scope the session to the given tenant for RLS enforcement.

    Mirrors get_tenant_db: drop to the non-superuser app_user role so RLS is
    actually enforced (the connection role bypasses it otherwise), then set the
    tenant GUC the policies compare against. Both are transaction-local.
    """
    await session.execute(text("SET LOCAL ROLE app_user"))
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )
