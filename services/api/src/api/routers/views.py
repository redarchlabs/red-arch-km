"""View routes.

Admin CRUD (org-admin) + a member-gated render endpoint. Views render through the
same contract as forms (``FormRenderRead``); the frontend walks the tree with the
shared ``FormRenderer`` and resolves any embedded ``form_ref`` widgets client-side.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db, get_tenant_db
from api.schemas.form import FormRenderRead
from api.schemas.view import ViewCreate, ViewRead, ViewShareCreated, ViewShareRequest, ViewUpdate
from api.schemas.workflow import ManualRunResult
from api.services.form_service import (
    FormConflictError,
    FormError,
    FormNotFoundError,
    FormValidationError,
)
from api.services.rate_limit import SlidingWindowLimiter
from api.services.view_service import ViewService
from api.services.view_share import (
    PublicViewService,
    ViewShareAdminService,
    ViewShareError,
    unsupported_elements,
)

router = APIRouter()
public_router = APIRouter()

_ERROR_STATUS = {
    FormConflictError: status.HTTP_409_CONFLICT,
    FormNotFoundError: status.HTTP_404_NOT_FOUND,
    FormValidationError: status.HTTP_400_BAD_REQUEST,
}


def _raise_http(exc: FormError) -> None:
    code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/", response_model=list[ViewRead])
async def list_views(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ViewRead]:
    views = await ViewService(session, ctx.org_id).list_views()
    return [ViewRead.model_validate(v) for v in views]


@router.post("/", response_model=ViewRead, status_code=status.HTTP_201_CREATED)
async def create_view(
    body: ViewCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ViewRead:
    try:
        view = await ViewService(session, ctx.org_id).create_view(body)
    except FormError as exc:
        _raise_http(exc)
    return ViewRead.model_validate(view)


@router.get("/{view_id}", response_model=ViewRead)
async def get_view(
    view_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ViewRead:
    try:
        view = await ViewService(session, ctx.org_id).get_view(view_id)
    except FormError as exc:
        _raise_http(exc)
    return ViewRead.model_validate(view)


@router.patch("/{view_id}", response_model=ViewRead)
async def update_view(
    view_id: uuid.UUID,
    body: ViewUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ViewRead:
    try:
        view = await ViewService(session, ctx.org_id).update_view(view_id, body)
    except FormError as exc:
        _raise_http(exc)
    return ViewRead.model_validate(view)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_view(
    view_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    try:
        await ViewService(session, ctx.org_id).delete_view(view_id)
    except FormError as exc:
        _raise_http(exc)


@router.get("/{view_id}/render", response_model=FormRenderRead)
async def render_view(
    view_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    record_id: str | None = None,
) -> FormRenderRead:
    # The sentinel ``record_id=me`` auto-binds the view to the current user's own
    # record in its root entity (matched by the entity's ``email`` field). Any
    # other value must parse as a UUID, so a malformed id still fails validation
    # (422) exactly as before.
    resolve_me = record_id == "me"
    parsed_id: uuid.UUID | None = None
    if record_id is not None and not resolve_me:
        try:
            parsed_id = uuid.UUID(record_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="record_id must be a UUID or 'me'",
            ) from exc
    try:
        return await ViewService(session, ctx.org_id).render(
            view_id,
            parsed_id,
            current_user_email=ctx.user.email if resolve_me else None,
        )
    except FormError as exc:
        _raise_http(exc)


# ------------------------------------------------------------------ #
# Anonymous access (org-admin to enable; unauthenticated to use)
#
# Off unless an admin turns it on for a specific view. See
# ``api.services.view_share`` for the full security model.
# ------------------------------------------------------------------ #
_ERROR_STATUS[ViewShareError] = status.HTTP_403_FORBIDDEN

# Per-token throttle on the unauthenticated endpoints so a leaked link cannot be
# hammered. Per-process, lazily sized from settings on first use — same shape as
# the public form limiter.
_public_limiter: SlidingWindowLimiter | None = None


def _rate_limit_public(token: str, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    global _public_limiter
    if _public_limiter is None:
        # A kiosk re-renders on a timer, so this ceiling is per token and sized for
        # a page that polls, not for a form that is submitted once. It is also shared
        # by EVERY device on the link, so it scales with the audience: see
        # `public_view_rate_limit_per_minute`, which replaced a flat 120 that a class
        # of phones exhausted after the fourth one scanned the QR code.
        _public_limiter = SlidingWindowLimiter(settings.public_view_rate_limit_per_minute)
    if not _public_limiter.allow(token):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down and try again shortly.",
        )


@router.post("/{view_id}/share", response_model=ViewShareCreated)
async def enable_view_share(
    view_id: uuid.UUID,
    body: ViewShareRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ViewShareCreated:
    """Turn on anonymous access, or ROTATE the existing link.

    Returns the raw token exactly once — only its hash is stored, so a link that
    is lost or over-shared is replaced, never recovered. Rotating invalidates the
    previous link immediately.
    """
    try:
        view, raw = await ViewShareAdminService(session, ctx.org_id).enable(
            view_id, record_id=body.record_id, expires_at=body.expires_at
        )
    except FormError as exc:
        _raise_http(exc)
    base = (settings.public_base_url or "").rstrip("/")
    return ViewShareCreated(
        url=f"{base}/s/{raw}",
        token=raw,
        expires_at=view.public_expires_at,
        record_id=view.public_record_id,
        unsupported_elements=unsupported_elements(view.config or {}),
    )


@router.delete("/{view_id}/share", response_model=ViewRead)
async def disable_view_share(
    view_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ViewRead:
    """Revoke anonymous access. The existing link stops working immediately."""
    try:
        view = await ViewShareAdminService(session, ctx.org_id).disable(view_id)
    except FormError as exc:
        _raise_http(exc)
    return ViewRead.model_validate(view)


@public_router.get("/{token}", response_model=FormRenderRead, dependencies=[Depends(_rate_limit_public)])
async def public_render_view(
    token: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FormRenderRead:
    """Render a shared view for someone with no login.

    The record is the one pinned when sharing was enabled — never taken from the
    request, so the link cannot be walked onto another row.
    """
    try:
        return await PublicViewService(session).render(token)
    except FormError as exc:
        _raise_http(exc)


@public_router.post(
    "/{token}/workflows/{workflow_id}/run",
    response_model=ManualRunResult,
    dependencies=[Depends(_rate_limit_public)],
)
async def public_run_view_workflow(
    token: str,
    workflow_id: uuid.UUID,
    body: dict[str, Any],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ManualRunResult:
    """Run one of THIS view's workflows anonymously.

    Bounded by the view's own element tree: a workflow the page does not reference
    is rejected, so a leaked link can do what the page does and nothing more.
    """
    try:
        return await PublicViewService(session).run_workflow(
            token,
            workflow_id,
            inputs=body.get("inputs") if isinstance(body.get("inputs"), dict) else None,
            after=body.get("after") if isinstance(body.get("after"), dict) else None,
            settings=settings,
        )
    except FormError as exc:
        _raise_http(exc)
