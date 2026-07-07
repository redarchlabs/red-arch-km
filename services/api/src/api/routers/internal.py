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
from api.services.crypto import decrypt_secret

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

    The worker (the only cross-service consumer) calls this — not the Celery
    payload — so the secret never rides the broker. The key is stored encrypted
    at rest (see services/crypto.py); we decrypt it here before returning. A null
    key tells the worker to fall back to the central OPENAI_API_KEY. Uses a
    dedicated RLS-scoped session, mirroring the status callback — the worker has
    no user JWT. Never log the decrypted value.
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
        stored = org.openai_api_key
        plaintext = (
            decrypt_secret(stored, settings.org_encryption_key.get_secret_value())
            if stored
            else None
        )
        return OrgOpenAIKey(openai_api_key=plaintext)


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
    (``FOR UPDATE SKIP LOCKED``) sees every tenant. The dispatcher then downgrades
    to ``app_user`` + sets ``app.current_tenant_id`` PER EVENT (see
    ``WorkflowDispatchService._enter_tenant``), so every action write is a real
    RLS-enforced write scoped to that event's org — a privileged session is used
    only for the cross-org claim, never for the per-tenant action writes.
    """
    from api.services.email import EmailSender
    from api.services.workflow.dispatcher import WorkflowDispatchService

    allowlist = tuple(getattr(settings, "workflow_webhook_allowlist", ()) or ())
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            service = WorkflowDispatchService(
                session,
                webhook_allowlist=allowlist,
                trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
                public_base_url=settings.public_base_url,
                email_sender=EmailSender(settings),
            org_encryption_key=settings.org_encryption_key.get_secret_value(),
            )
            counters = await service.process_pending(limit=limit)
            await session.commit()
            return DispatchResult(**{k: counters.get(k, 0) for k in ("events", "runs", "actions", "skipped")})
        except Exception:
            await session.rollback()
            logger.exception("workflow dispatch batch failed")
            raise


class TimerResult(BaseModel):
    """Counters returned by a workflow run-timers sweep."""

    resumed: int = 0
    scheduled: int = 0
    actions: int = 0


@router.post(
    "/workflows/run-timers",
    dependencies=[Depends(require_internal_api_key)],
    response_model=TimerResult,
)
async def run_workflow_timers(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TimerResult:
    """Resume delayed runs that are due + fire due scheduled workflows.

    Time-based work (unlike the change-driven outbox sweep): runs on the
    privileged session so the cross-org scan/claim sees every tenant. As with
    dispatch-batch, the dispatcher downgrades to ``app_user`` + the tenant GUC
    per resumed run / per scheduled workflow, so each unit's writes are RLS-
    enforced for that org rather than running privileged.
    """
    from api.services.email import EmailSender
    from api.services.workflow.dispatcher import WorkflowDispatchService

    allowlist = tuple(settings.workflow_webhook_allowlist or ())
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            service = WorkflowDispatchService(
                session,
                webhook_allowlist=allowlist,
                trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
                public_base_url=settings.public_base_url,
                email_sender=EmailSender(settings),
            org_encryption_key=settings.org_encryption_key.get_secret_value(),
            )
            counters = await service.process_timers()
            await session.commit()
            return TimerResult(**{k: counters.get(k, 0) for k in ("resumed", "scheduled", "actions")})
        except Exception:
            await session.rollback()
            logger.exception("workflow run-timers sweep failed")
            raise


class TokenSweepResult(BaseModel):
    """Counters returned by a token-engine sweep."""

    reactivated: int = 0
    claimed: int = 0
    advanced: int = 0


@router.post(
    "/workflows/advance-tokens",
    dependencies=[Depends(require_internal_api_key)],
    response_model=TokenSweepResult,
)
async def advance_workflow_tokens(
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> TokenSweepResult:
    """Drive the BPMN token engine: reactivate parked tokens whose wait elapsed
    (timers / retries / crashed leases), then drain the active token queue.

    Runs on the privileged session so the cross-org claim sees every tenant; the
    engine downgrades to ``app_user`` + the tenant GUC per token, so each token's
    side effects are RLS-enforced for its org. Most v2 runs complete synchronously
    inside dispatch — this sweep exists to resume parked tokens and to make
    progress if a synchronous drive was interrupted.
    """
    from api.services.email import EmailSender
    from api.services.workflow.engine import TokenEngine

    allowlist = tuple(settings.workflow_webhook_allowlist or ())
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            engine = TokenEngine(
                session,
                webhook_allowlist=allowlist,
                trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
                public_base_url=settings.public_base_url,
                email_sender=EmailSender(settings),
            org_encryption_key=settings.org_encryption_key.get_secret_value(),
            )
            resumed = await engine.resume_due_tokens(limit=limit)
            await session.commit()
            totals = {"reactivated": resumed.get("reactivated", 0), "claimed": 0, "advanced": 0}
            # Bounded drain: keep advancing until a pass claims nothing (or we hit
            # the pass cap, leaving the rest for the next tick).
            for _ in range(20):
                delta = await engine.advance_tokens(limit=limit)
                await session.commit()
                totals["claimed"] += delta.get("claimed", 0)
                totals["advanced"] += delta.get("advanced", 0)
                if delta.get("claimed", 0) == 0:
                    break
            return TokenSweepResult(**totals)
        except Exception:
            await session.rollback()
            logger.exception("workflow advance-tokens sweep failed")
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
