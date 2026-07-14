"""Inbound configuration-promotion receiver (``/api/v1/config``).

This is the *remote* side of a cross-instance promotion: another KM2 deployment
pushes a config bundle here over HTTPS, authenticated by an org API key holding
the ``config:write`` scope. We apply it to that key's org and return the result
(including a reverse snapshot the source stores so it can roll back).

``config:write`` is deliberately sensitive — it can create/update/delete entities,
workflows, and agents — so it is never granted by a wildcard (see
``api_key_scopes``), and the apply runs on the DDL-capable, still-RLS-scoped owner
session (``get_apikey_tenant_owner_db``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_owner_db, require_scope
from api.config import Settings, get_settings
from api.routers.migration import _read_bundle
from api.services.migration.bundle import BUNDLE_FORMAT_VERSION, CollisionStrategy
from api.services.migration.promotion import PromotionBlocked, PromotionExecutor, PromotionResult

router = APIRouter()


@router.get("/ping")
async def ping(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("config:read"))],
) -> dict[str, object]:
    """Cheap authenticated probe for the source's 'test connection' button:
    confirms the key is valid, has config access, and reports the bundle format
    this instance speaks (so the source can warn on a version mismatch)."""
    return {
        "ok": True,
        "org_id": str(principal.org_id),
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
    }


@router.post("/promotions", response_model=PromotionResult)
async def receive_promotion(
    file: UploadFile,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("config:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_owner_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    strategy: Annotated[CollisionStrategy, Query()] = CollisionStrategy.SKIP,
    dry_run: Annotated[bool, Query()] = False,
    apply_deletes: Annotated[bool, Query()] = False,
    allow_data: Annotated[bool, Query()] = False,
    override_inflight: Annotated[bool, Query()] = False,
    idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key")] = None,
) -> PromotionResult:
    """Apply a pushed config bundle to this key's org.

    ``dry_run`` previews (diff + summary, nothing persisted). Otherwise the bundle
    is applied and the result — including the reverse snapshot for rollback — is
    returned. A destructive apply blocked by in-flight workflow runs returns 409
    with the blockers (the source can retry with ``override_inflight=true``).

    ``X-Idempotency-Key`` is accepted for a retried push; enforced de-duplication is
    a hardening follow-up (config apply with skip/overwrite is naturally idempotent).
    """
    bundle = await _read_bundle(file)
    executor = PromotionExecutor(session, principal.org_id, settings)

    if dry_run:
        result = await executor.preview(bundle, strategy=strategy, apply_deletes=apply_deletes)
        await session.rollback()
        return result

    try:
        result, importer = await executor.promote(
            bundle,
            strategy=strategy,
            apply_deletes=apply_deletes,
            allow_data=allow_data,
            override_inflight=override_inflight,
        )
    except PromotionBlocked as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Blocked by in-flight workflow runs on the target instance.",
                "blockers": [b.model_dump() for b in exc.blockers],
            },
        ) from exc

    await session.commit()
    await importer.dispatch_pending_ingests(result.import_summary)  # type: ignore[arg-type]
    return result
