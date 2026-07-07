"""View routes.

Admin CRUD (org-admin) + a member-gated render endpoint. Views render through the
same contract as forms (``FormRenderRead``); the frontend walks the tree with the
shared ``FormRenderer`` and resolves any embedded ``form_ref`` widgets client-side.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.dependencies import get_tenant_db
from api.schemas.form import FormRenderRead
from api.schemas.view import ViewCreate, ViewRead, ViewUpdate
from api.services.form_service import (
    FormConflictError,
    FormError,
    FormNotFoundError,
    FormValidationError,
)
from api.services.view_service import ViewService

router = APIRouter()

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
    record_id: uuid.UUID | None = None,
) -> FormRenderRead:
    try:
        return await ViewService(session, ctx.org_id).render(view_id, record_id)
    except FormError as exc:
        _raise_http(exc)
