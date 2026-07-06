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
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.config import Settings, get_settings
from api.dependencies import get_db, get_tenant_db
from api.schemas.form import (
    FormCreate,
    FormLinkCreated,
    FormLinkRead,
    FormRead,
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
    FormService,
    FormValidationError,
    PublicFormService,
)

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
# Public (unauthenticated) — resolves org from the token
# ------------------------------------------------------------------ #
@public_router.get("/{token}", response_model=PublicFormRead)
async def public_get_form(
    token: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PublicFormRead:
    try:
        return await PublicFormService(session).load(token)
    except FormError as exc:
        _raise_http(exc)


@public_router.post("/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def public_submit_form(
    token: str,
    body: PublicFormSubmit,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    try:
        await PublicFormService(session).submit(token, body)
    except FormError as exc:
        _raise_http(exc)
