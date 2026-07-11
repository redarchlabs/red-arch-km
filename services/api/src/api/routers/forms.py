"""Intake-form routes.

``router`` — authenticated org-admin CRUD + link minting, on the tenant session.
``public_router`` — the unauthenticated public form (render + submit). It runs on
the privileged session because it must resolve the org from the token before any
tenant context exists; ``PublicFormService`` scopes to that org immediately after.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db, get_tenant_db
from api.schemas.form import (
    FormCreate,
    FormLinkCreated,
    FormLinkRead,
    FormRead,
    FormRenderRead,
    FormSubmit,
    FormUpdate,
    GenerateLinkRequest,
    PublicFormRead,
    PublicFormSubmit,
)
from api.services.email import EmailSender
from api.services.form_service import (
    FormConflictError,
    FormError,
    FormLinkError,
    FormNotFoundError,
    FormRenderService,
    FormService,
    FormValidationError,
    PublicFormService,
)
from api.services.rate_limit import SlidingWindowLimiter

router = APIRouter()
public_router = APIRouter()

_ERROR_STATUS = {
    FormConflictError: status.HTTP_409_CONFLICT,
    FormNotFoundError: status.HTTP_404_NOT_FOUND,
    FormValidationError: status.HTTP_400_BAD_REQUEST,
    FormLinkError: status.HTTP_410_GONE,
}


def _raise_http(exc: FormError) -> None:
    code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _service(session: AsyncSession, ctx: OrgContext, settings: Settings) -> FormService:
    return FormService(
        session,
        ctx.org_id,
        public_base_url=settings.public_base_url,
        email_sender=EmailSender(settings),
    )


# Per-token throttle for the unauthenticated public endpoints so a leaked token
# can't be hammered. Per-process, lazily sized from settings on first use.
_public_limiter: SlidingWindowLimiter | None = None


def _rate_limit_public(
    token: str, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    global _public_limiter
    if _public_limiter is None:
        _public_limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)
    if not _public_limiter.allow(token):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down and try again shortly.",
        )


# ------------------------------------------------------------------ #
# Admin (org-admin)
# ------------------------------------------------------------------ #
@router.get("/", response_model=list[FormRead])
async def list_forms(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[FormRead]:
    forms = await _service(session, ctx, settings).list_forms()
    return [FormRead.model_validate(f) for f in forms]


@router.post("/", response_model=FormRead, status_code=status.HTTP_201_CREATED)
async def create_form(
    body: FormCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FormRead:
    try:
        form = await _service(session, ctx, settings).create_form(body)
    except FormError as exc:
        _raise_http(exc)
    return FormRead.model_validate(form)


@router.get("/{form_id}", response_model=FormRead)
async def get_form(
    form_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FormRead:
    try:
        form = await _service(session, ctx, settings).get_form(form_id)
    except FormError as exc:
        _raise_http(exc)
    return FormRead.model_validate(form)


@router.patch("/{form_id}", response_model=FormRead)
async def update_form(
    form_id: uuid.UUID,
    body: FormUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FormRead:
    try:
        form = await _service(session, ctx, settings).update_form(form_id, body)
    except FormError as exc:
        _raise_http(exc)
    return FormRead.model_validate(form)


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        await _service(session, ctx, settings).delete_form(form_id)
    except FormError as exc:
        _raise_http(exc)


@router.get("/{form_id}/links", response_model=list[FormLinkRead])
async def list_links(
    form_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[FormLinkRead]:
    try:
        links = await _service(session, ctx, settings).list_links(form_id)
    except FormError as exc:
        _raise_http(exc)
    return [FormLinkRead.model_validate(link) for link in links]


@router.post("/{form_id}/links/{link_id}/revoke", response_model=FormLinkRead)
async def revoke_link(
    form_id: uuid.UUID,
    link_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FormLinkRead:
    try:
        link = await _service(session, ctx, settings).revoke_link(form_id, link_id)
    except FormError as exc:
        _raise_http(exc)
    return FormLinkRead.model_validate(link)


@router.post("/{form_id}/links", response_model=FormLinkCreated, status_code=status.HTTP_201_CREATED)
async def generate_link(
    form_id: uuid.UUID,
    body: GenerateLinkRequest,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FormLinkCreated:
    try:
        link, raw_token, url, email_sent = await _service(session, ctx, settings).generate_link(
            form_id, body
        )
    except FormError as exc:
        _raise_http(exc)
    return FormLinkCreated(
        id=link.id,
        form_id=link.form_id,
        status=link.status,
        recipient_email=link.recipient_email,
        expires_at=link.expires_at,
        submitted_at=link.submitted_at,
        token=raw_token,
        url=url,
        email_sent=email_sent,
    )


# ------------------------------------------------------------------ #
# Authenticated internal fill surface (any org member) — no token needed.
# Same render/submit core as the public path, on the caller's tenant session.
# ------------------------------------------------------------------ #
class InternalSubmit(FormSubmit):
    model_config = ConfigDict(extra="forbid")
    record_id: uuid.UUID


@router.get("/{form_id}/render", response_model=FormRenderRead)
async def render_form(
    form_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    record_id: uuid.UUID | None = None,
) -> FormRenderRead:
    try:
        form = await _service(session, ctx, settings).get_form(form_id)
        return await FormRenderService(session, ctx.org_id).build_render(form, record_id, "editable")
    except FormError as exc:
        _raise_http(exc)


@router.post("/{form_id}/submit", status_code=status.HTTP_204_NO_CONTENT)
async def submit_form(
    form_id: uuid.UUID,
    body: InternalSubmit,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        form = await _service(session, ctx, settings).get_form(form_id)
        payload = FormSubmit(values=body.values, related=body.related)
        await FormRenderService(session, ctx.org_id).apply_submit(form, body.record_id, payload)
    except FormError as exc:
        _raise_http(exc)


# ------------------------------------------------------------------ #
# Public (unauthenticated) — resolves org from the token
# ------------------------------------------------------------------ #
@public_router.get(
    "/{token}", response_model=PublicFormRead, dependencies=[Depends(_rate_limit_public)]
)
async def public_get_form(
    token: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PublicFormRead:
    try:
        return await PublicFormService(session).load(token)
    except FormError as exc:
        _raise_http(exc)


@public_router.post(
    "/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_rate_limit_public)],
)
async def public_submit_form(
    token: str,
    body: PublicFormSubmit,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    try:
        await PublicFormService(session).submit(token, body)
    except FormError as exc:
        _raise_http(exc)
