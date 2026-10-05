"""API-key management — the org-admin surface behind the Admin Area "API" tab.

These routes are authenticated the normal (Clerk / browser) way and gated to org
admins; they mint and revoke the org's programmatic API keys. They are NOT the
key-authenticated public surface — that is ``/api/v1`` (see ``routers/v1``).

The plaintext key is returned exactly once, from ``POST /``. Everything else
exposes metadata only.

A key may be minted with region/role/group/department assignments and/or folders,
which makes it *scoped*: it reads knowledge with the masks of its own assignments,
narrowed to those folders (and their subfolders). Assignments are fixed at mint;
there is no update route — revoke and re-issue instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.models.api_key import ApiKey
from api.schemas.api_key import (
    ApiKeyAccessMode,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ApiKeyStatus,
    NamedRef,
    ScopeInfo,
)
from api.services.api_key_assignments import AssignmentLabels, KeyAssignments
from api.services.api_key_scopes import API_SCOPES
from api.services.api_key_service import (
    ApiKeyAssignmentInvalid,
    ApiKeyConflictError,
    ApiKeyError,
    ApiKeyNotFoundError,
    ApiKeyService,
    ApiKeyValidationError,
    is_expired,
)

router = APIRouter()

_ASSIGNMENT_KINDS = ("regions", "roles", "groups", "departments", "folders")

_ERROR_STATUS = {
    ApiKeyNotFoundError: status.HTTP_404_NOT_FOUND,
    ApiKeyValidationError: status.HTTP_400_BAD_REQUEST,
    ApiKeyAssignmentInvalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ApiKeyConflictError: status.HTTP_409_CONFLICT,
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


def _assignments_note(status: ApiKeyStatus, access_mode: ApiKeyAccessMode, assigned: AssignmentLabels) -> str | None:
    """Why a scoped key shows no assignments. A revoked or expired key's rows are
    released when one of its items is deleted — that is not a broken key."""
    if access_mode != "scoped" or any(getattr(assigned, kind) for kind in _ASSIGNMENT_KINDS):
        return None
    if status != "active":
        return f"Assignments released after the key was {status}"
    return "No assignments left — this key is refused"


def _refs(items: list[tuple[uuid.UUID, str]]) -> list[NamedRef]:
    return [NamedRef(id=i, name=n) for i, n in items]


def _to_read(api_key: ApiKey, labels: dict[uuid.UUID, AssignmentLabels] | None = None) -> ApiKeyRead:
    assigned = (labels or {}).get(api_key.id) or AssignmentLabels()
    access_mode: ApiKeyAccessMode = "scoped" if api_key.access_mode == "scoped" else "org"
    key_status = _status_of(api_key)
    return ApiKeyRead(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=list(api_key.scopes or ()),
        status=key_status,
        created_by_profile_id=api_key.created_by_profile_id,
        access_mode=access_mode,
        regions=_refs(assigned.regions),
        roles=_refs(assigned.roles),
        groups=_refs(assigned.groups),
        departments=_refs(assigned.departments),
        folders=_refs(assigned.folders),
        assignments_note=_assignments_note(key_status, access_mode, assigned),
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
    service = ApiKeyService(session, ctx.org_id)
    keys = await service.list_keys()
    labels = await service.assignment_labels(keys)
    return [_to_read(k, labels) for k in keys]


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
    service = ApiKeyService(session, ctx.org_id)
    try:
        api_key, plaintext = await service.create_key(
            name=body.name,
            scopes=body.scopes,
            expires_at=expires_at,
            created_by_profile_id=ctx.user.profile_id,
            assignments=KeyAssignments.of(
                regions=body.region_ids,
                roles=body.role_ids,
                groups=body.group_ids,
                departments=body.department_ids,
                folders=body.folder_ids,
            ),
        )
    except ApiKeyError as exc:
        _raise_http(exc)
    # Commit HERE, not in the session teardown (which runs after the response is
    # sent): the plaintext must never leave for a key that did not persist.
    await session.commit()
    await db_scope.enter_tenant(session, ctx.org_id)
    read = _to_read(api_key, await service.assignment_labels([api_key]))
    return ApiKeyCreated(**read.model_dump(), key=plaintext)


@router.delete("/{api_key_id}", response_model=ApiKeyRead)
async def revoke_api_key(
    api_key_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ApiKeyRead:
    """Revoke a key immediately (idempotent). Returns the updated metadata."""
    service = ApiKeyService(session, ctx.org_id)
    try:
        api_key = await service.revoke_key(api_key_id)
    except ApiKeyError as exc:
        _raise_http(exc)
    return _to_read(api_key, await service.assignment_labels([api_key]))
