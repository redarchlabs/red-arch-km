"""API-key management — the org-admin surface behind the Admin Area "API" tab.

These routes are authenticated the normal (Clerk / browser) way and gated to org
admins; they mint and revoke the org's programmatic API keys. They are NOT the
key-authenticated public surface — that is ``/api/v1`` (see ``routers/v1``).

The plaintext key is returned exactly once, from ``POST /``. Everything else
exposes metadata only.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.models.api_key import ApiKey
from api.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, ApiKeyStatus, ScopeInfo
from api.services.api_key_scopes import API_SCOPES
from api.services.api_key_service import (
    ApiKeyError,
    ApiKeyNotFoundError,
    ApiKeyService,
    ApiKeyValidationError,
    is_expired,
)

router = APIRouter()

_ERROR_STATUS = {
    ApiKeyNotFoundError: status.HTTP_404_NOT_FOUND,
    ApiKeyValidationError: status.HTTP_400_BAD_REQUEST,
}


def _raise_http(exc: ApiKeyError) -> NoReturn:
    code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _status_of(api_key: ApiKey) -> ApiKeyStatus:
    if api_key.revoked_at is not None:
        return "revoked"
    if is_expired(api_key):
        return "expired"
    return "active"


def _to_read(api_key: ApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=list(api_key.scopes or ()),
        status=_status_of(api_key),
        created_by_profile_id=api_key.created_by_profile_id,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        revoked_at=api_key.revoked_at,
        created_at=api_key.created_at,
    )


@router.get("/scopes", response_model=list[ScopeInfo])
async def list_scopes(
    _ctx: Annotated[OrgContext, Depends(require_org_admin)],
) -> list[ScopeInfo]:
    """The scope catalog, so the create form can render labelled checkboxes."""
    return [ScopeInfo(name=s.name, description=s.description) for s in API_SCOPES]


@router.get("/", response_model=list[ApiKeyRead])
async def list_api_keys(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ApiKeyRead]:
    keys = await ApiKeyService(session, ctx.org_id).list_keys()
    return [_to_read(k) for k in keys]


@router.post("/", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ApiKeyCreated:
    """Mint a key. The plaintext ``key`` in the response is shown ONCE."""
    expires_at = body.expires_at
    # Normalize to an aware UTC instant so the "future" check + storage agree.
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    try:
        api_key, plaintext = await ApiKeyService(session, ctx.org_id).create_key(
            name=body.name,
            scopes=body.scopes,
            expires_at=expires_at,
            created_by_profile_id=ctx.user.profile_id,
        )
    except ApiKeyError as exc:
        _raise_http(exc)
    read = _to_read(api_key)
    return ApiKeyCreated(**read.model_dump(), key=plaintext)


@router.delete("/{api_key_id}", response_model=ApiKeyRead)
async def revoke_api_key(
    api_key_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ApiKeyRead:
    """Revoke a key immediately (idempotent). Returns the updated metadata."""
    try:
        api_key = await ApiKeyService(session, ctx.org_id).revoke_key(api_key_id)
    except ApiKeyError as exc:
        _raise_http(exc)
    return _to_read(api_key)
