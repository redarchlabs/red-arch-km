"""Change-management control plane: releases, targets, promotion, and rollback.

Org-admin only (Clerk session), on the privileged ``get_db`` session with explicit
``org_id`` scoping — mirroring ``/api/migration``. A **local-org** promotion writes
into another org in this database, so it additionally requires the caller to be an
admin of BOTH the source and the target org (RLS cannot enforce that across orgs;
:func:`_require_admin_of` does).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.models.promotion import (
    PromotionTarget,
    PromotionTargetKind,
    Release,
    ReleaseApproval,
    ReleasePromotion,
)
from api.models.user import UserOrgMembership
from api.services.migration.bundle import CollisionStrategy, GeneratedSecret, Selection
from api.services.migration.diff import BundleDiff
from api.services.migration.promotion import PromotionBlocked
from api.services.promotion_service import PromotionError, PromotionService

router = APIRouter()


# --------------------------------------------------------------------------- #
# Cross-org authorization
# --------------------------------------------------------------------------- #
async def _require_admin_of(session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID) -> None:
    """A local-org promotion writes into ``org_id``; the caller must be an admin
    there too (or a site admin). RLS can't enforce cross-org, so we check here."""
    if ctx.user.is_site_admin:
        return
    row = await session.execute(
        select(UserOrgMembership).where(
            UserOrgMembership.profile_id == ctx.user.profile_id,
            UserOrgMembership.org_id == org_id,
        )
    )
    membership = row.scalar_one_or_none()
    if membership is None or not membership.is_org_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an admin of the target organization to promote into it.",
        )


def _svc(ctx: OrgContext, session: AsyncSession, settings: Settings) -> PromotionService:
    return PromotionService(session, ctx.org_id, settings)


def _400(exc: PromotionError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class TargetCreate(BaseModel):
    name: str = Field(max_length=120)
    kind: PromotionTargetKind
    target_org_id: uuid.UUID | None = None
    base_url: str | None = None
    remote_org_id: uuid.UUID | None = None
    api_key: str | None = None  # write-only; never returned
    config: dict[str, Any] | None = None


class TargetOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    enabled: bool
    target_org_id: uuid.UUID | None
    base_url: str | None
    remote_org_id: uuid.UUID | None
    has_key: bool
    config: dict[str, Any]

    @classmethod
    def of(cls, t: PromotionTarget) -> TargetOut:
        return cls(
            id=t.id,
            name=t.name,
            kind=t.kind,
            enabled=t.enabled,
            target_org_id=t.target_org_id,
            base_url=t.base_url,
            remote_org_id=t.remote_org_id,
            has_key=bool(t.secret_encrypted),
            config=t.config or {},
        )


class ReleaseCreate(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = None
    selection: Selection | None = None


class ReleaseOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    bundle_hash: str | None
    bundle_format_version: int | None
    created_by_id: uuid.UUID | None

    @classmethod
    def of(cls, r: Release) -> ReleaseOut:
        return cls(
            id=r.id,
            name=r.name,
            description=r.description,
            status=r.status,
            bundle_hash=r.bundle_hash,
            bundle_format_version=r.bundle_format_version,
            created_by_id=r.created_by_id,
        )


class ApprovalIn(BaseModel):
    comment: str | None = None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    approver_id: uuid.UUID | None
    decision: str
    comment: str | None

    @classmethod
    def of(cls, a: ReleaseApproval) -> ApprovalOut:
        return cls(id=a.id, approver_id=a.approver_id, decision=a.decision, comment=a.comment)


class PromotionOut(BaseModel):
    id: uuid.UUID
    release_id: uuid.UUID
    target_id: uuid.UUID | None
    target_kind: str
    target_label: str
    target_org_id: uuid.UUID | None
    status: str
    strategy: str
    promoted_by_id: uuid.UUID | None
    result_summary: dict[str, Any] | None
    rollback_source_id: uuid.UUID | None

    @classmethod
    def of(cls, p: ReleasePromotion) -> PromotionOut:
        return cls(
            id=p.id,
            release_id=p.release_id,
            target_id=p.target_id,
            target_kind=p.target_kind,
            target_label=p.target_label,
            target_org_id=p.target_org_id,
            status=p.status,
            strategy=p.strategy,
            promoted_by_id=p.promoted_by_id,
            result_summary=p.result_summary,
            rollback_source_id=p.rollback_source_id,
        )


class ReleaseItemOut(BaseModel):
    object_type: str
    lineage_id: uuid.UUID
    natural_key: str | None


class ReleaseDetail(BaseModel):
    release: ReleaseOut
    items: list[ReleaseItemOut]
    approvals: list[ApprovalOut]
    promotions: list[PromotionOut]


# Promoting a release means "make the target match the release", so the default
# strategy is OVERWRITE (update existing objects), not SKIP (which would leave
# every already-present object untouched and only create net-new ones).
class DiffRequest(BaseModel):
    target_id: uuid.UUID
    strategy: CollisionStrategy = CollisionStrategy.OVERWRITE
    apply_deletes: bool = False


class PromoteRequest(BaseModel):
    target_id: uuid.UUID
    strategy: CollisionStrategy = CollisionStrategy.OVERWRITE
    apply_deletes: bool = False
    allow_data: bool = False
    override_inflight: bool = False


class PromoteResponse(BaseModel):
    promotion: PromotionOut
    diff: BundleDiff
    generated_secrets: list[GeneratedSecret]


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
@router.get("/targets", response_model=list[TargetOut])
async def list_targets(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[TargetOut]:
    return [TargetOut.of(t) for t in await _svc(ctx, session, settings).list_targets()]


@router.post("/targets", response_model=TargetOut, status_code=status.HTTP_201_CREATED)
async def create_target(
    body: TargetCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TargetOut:
    # For a local-org target, the caller must administer the target org too.
    if body.kind is PromotionTargetKind.LOCAL_ORG and body.target_org_id is not None:
        await _require_admin_of(session, ctx, body.target_org_id)
    try:
        target = await _svc(ctx, session, settings).create_target(
            name=body.name,
            kind=body.kind,
            target_org_id=body.target_org_id,
            base_url=body.base_url,
            remote_org_id=body.remote_org_id,
            api_key=body.api_key,
            config=body.config,
        )
    except PromotionError as exc:
        raise _400(exc) from exc
    await session.commit()
    return TargetOut.of(target)


class TestResult(BaseModel):
    ok: bool
    remote_bundle_format_version: int | None = None
    error: str | None = None


@router.post("/targets/{target_id}/test", response_model=TestResult)
async def test_target(
    target_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TestResult:
    """Probe a remote target: reachability + whether its API key has config access."""
    svc = _svc(ctx, session, settings)
    target = await svc.get_target(target_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="target not found")
    try:
        result = await svc.test_target(target)
    except PromotionError as exc:
        raise _400(exc) from exc
    return TestResult(**result)


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        await _svc(ctx, session, settings).delete_target(target_id)
    except PromotionError as exc:
        raise _400(exc) from exc
    await session.commit()


# --------------------------------------------------------------------------- #
# Releases
# --------------------------------------------------------------------------- #
@router.get("/releases", response_model=list[ReleaseOut])
async def list_releases(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ReleaseOut]:
    return [ReleaseOut.of(r) for r in await _svc(ctx, session, settings).list_releases()]


@router.post("/releases", response_model=ReleaseOut, status_code=status.HTTP_201_CREATED)
async def create_release(
    body: ReleaseCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReleaseOut:
    try:
        release = await _svc(ctx, session, settings).create_release(
            name=body.name,
            description=body.description,
            selection=body.selection,
            created_by_id=ctx.user.profile_id,
        )
    except PromotionError as exc:
        await session.rollback()
        raise _400(exc) from exc
    await session.commit()
    return ReleaseOut.of(release)


@router.get("/releases/{release_id}", response_model=ReleaseDetail)
async def get_release(
    release_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReleaseDetail:
    svc = _svc(ctx, session, settings)
    release = await svc.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="release not found")
    items = await svc.list_items(release_id)
    approvals = await svc.list_approvals(release_id)
    promotions = await svc.list_promotions(release_id=release_id)
    return ReleaseDetail(
        release=ReleaseOut.of(release),
        items=[
            ReleaseItemOut(object_type=i.object_type, lineage_id=i.lineage_id, natural_key=i.natural_key)
            for i in items
        ],
        approvals=[ApprovalOut.of(a) for a in approvals],
        promotions=[PromotionOut.of(p) for p in promotions],
    )


@router.post("/releases/{release_id}/submit", response_model=ReleaseOut)
async def submit_release(
    release_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReleaseOut:
    try:
        release = await _svc(ctx, session, settings).submit_release(release_id)
    except PromotionError as exc:
        raise _400(exc) from exc
    await session.commit()
    return ReleaseOut.of(release)


@router.post("/releases/{release_id}/approve", response_model=ReleaseOut)
async def approve_release(
    release_id: uuid.UUID,
    body: ApprovalIn,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReleaseOut:
    try:
        release = await _svc(ctx, session, settings).approve_release(
            release_id, approver_id=ctx.user.profile_id, comment=body.comment
        )
    except PromotionError as exc:
        raise _400(exc) from exc
    await session.commit()
    return ReleaseOut.of(release)


@router.post("/releases/{release_id}/reject", response_model=ReleaseOut)
async def reject_release(
    release_id: uuid.UUID,
    body: ApprovalIn,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReleaseOut:
    try:
        release = await _svc(ctx, session, settings).reject_release(
            release_id, approver_id=ctx.user.profile_id, comment=body.comment
        )
    except PromotionError as exc:
        raise _400(exc) from exc
    await session.commit()
    return ReleaseOut.of(release)


# --------------------------------------------------------------------------- #
# Diff / Promote / Rollback
# --------------------------------------------------------------------------- #
@router.post("/releases/{release_id}/diff", response_model=BundleDiff)
async def diff_release(
    release_id: uuid.UUID,
    body: DiffRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BundleDiff:
    # A local-org diff exports the *target* org's entire config; the caller must
    # administer that org too (same dual-org check as promote/rollback). Without
    # this, any source-org admin could read another org's config inventory.
    svc = _svc(ctx, session, settings)
    target = await svc.get_target(body.target_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="target not found")
    if target.kind == PromotionTargetKind.LOCAL_ORG.value and target.target_org_id is not None:
        await _require_admin_of(session, ctx, target.target_org_id)
    try:
        result = await svc.preview_promotion(
            release_id, body.target_id, strategy=body.strategy, apply_deletes=body.apply_deletes
        )
    except PromotionError as exc:
        raise _400(exc) from exc
    return result.diff


@router.post("/releases/{release_id}/promote", response_model=PromoteResponse)
async def promote_release(
    release_id: uuid.UUID,
    body: PromoteRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PromoteResponse:
    svc = _svc(ctx, session, settings)
    target = await svc.get_target(body.target_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="target not found")
    if target.kind == PromotionTargetKind.LOCAL_ORG.value and target.target_org_id is not None:
        await _require_admin_of(session, ctx, target.target_org_id)
    try:
        promotion, result = await svc.promote_release(
            release_id,
            body.target_id,
            strategy=body.strategy,
            apply_deletes=body.apply_deletes,
            allow_data=body.allow_data,
            override_inflight=body.override_inflight,
            promoted_by_id=ctx.user.profile_id,
        )
    except PromotionBlocked as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Blocked by in-flight workflow runs. Wait for them to finish or override.",
                "blockers": [b.model_dump() for b in exc.blockers],
            },
        ) from exc
    except PromotionError as exc:
        await session.rollback()
        raise _400(exc) from exc
    secrets = result.import_summary.generated_secrets if result.import_summary else []
    return PromoteResponse(promotion=PromotionOut.of(promotion), diff=result.diff, generated_secrets=secrets)


@router.get("", response_model=list[PromotionOut])
async def list_promotions(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    release_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[PromotionOut]:
    return [PromotionOut.of(p) for p in await _svc(ctx, session, settings).list_promotions(release_id=release_id)]


@router.post("/{promotion_id}/rollback", response_model=PromotionOut)
async def rollback_promotion(
    promotion_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PromotionOut:
    svc = _svc(ctx, session, settings)
    existing = await svc.get_promotion(promotion_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="promotion not found")
    # Only a LOCAL_ORG promotion writes into another org in *this* DB, so the
    # dual-admin check applies there. For a remote promotion ``target_org_id`` is an
    # org id on the *remote* instance (meaningless against local memberships) and
    # authorization is the stored remote API key.
    if existing.target_kind == PromotionTargetKind.LOCAL_ORG.value and existing.target_org_id is not None:
        await _require_admin_of(session, ctx, existing.target_org_id)
    try:
        promotion, _ = await svc.rollback_promotion(promotion_id, rolled_back_by_id=ctx.user.profile_id)
    except PromotionError as exc:
        await session.rollback()
        raise _400(exc) from exc
    return PromotionOut.of(promotion)
