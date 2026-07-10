"""Custom-entity record CRUD.

Runs under ``get_tenant_db`` (``app_user`` + RLS): the catalog is read to build
a ``DynamicEntityRepository`` for the addressed entity, then records are
created/read/updated/deleted with tenant isolation enforced by the database.
Any org member with access may read/write records (``require_org_access``).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.dependencies import get_tenant_db
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import (
    DynamicEntityRepository,
    EntityRecordError,
    RecordCursor,
)
from api.repositories.workflow import OutboxWriter

router = APIRouter()


def _encode_cursor(cursor: RecordCursor) -> str:
    """Opaque, URL-safe token for a ``(created_at, id)`` keyset position."""
    raw = f"{cursor[0].isoformat()}|{cursor[1]}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(token: str) -> RecordCursor:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        created_at_str, id_str = raw.rsplit("|", 1)
        return datetime.fromisoformat(created_at_str), uuid.UUID(id_str)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor"
        ) from exc


async def _repo_for(session: AsyncSession, org_id: uuid.UUID, slug: str) -> DynamicEntityRepository:
    definition = await EntityDefinitionRepository(session, org_id).get_by_slug(slug)
    if definition is None or not definition.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition.id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition.id)
    # Capture record changes into the workflow outbox in the same transaction so
    # workflows fire reliably (at-least-once).
    return DynamicEntityRepository(
        session, org_id, definition, fields, rels, outbox=OutboxWriter(session)
    )


@router.get("/{slug}/records")
async def list_records(
    slug: str,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    order_by: Annotated[str | None, Query(max_length=63)] = None,
    order_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, Any]:
    """Keyset-paginated, optionally-searched record page.

    Pages by an opaque ``cursor`` (no OFFSET) so it scales to millions of rows;
    ``next_cursor`` is the token for the following page, or ``null`` at the end.
    ``q`` does a case-insensitive substring search across text columns.

    ``order_by`` (a field slug or a base column) + ``order_dir`` override the sort
    — used by the record-list view element (e.g. a "latest record" status board).
    A custom sort returns a single page (``next_cursor`` is ``null``); keyset
    pagination applies only to the default ``created_at`` DESC ordering.
    """
    repo = await _repo_for(session, ctx.org_id, slug)
    decoded = _decode_cursor(cursor) if cursor else None
    try:
        items, next_cursor = await repo.list(
            search=q, cursor=decoded, limit=limit, order_by=order_by, order_dir=order_dir
        )
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "items": items,
        "next_cursor": _encode_cursor(next_cursor) if next_cursor else None,
        "limit": limit,
    }


@router.post("/{slug}/records", status_code=status.HTTP_201_CREATED)
async def create_record(
    slug: str,
    body: dict[str, Any],
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    repo = await _repo_for(session, ctx.org_id, slug)
    try:
        return await repo.create(body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{slug}/records/{record_id}")
async def get_record(
    slug: str,
    record_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    repo = await _repo_for(session, ctx.org_id, slug)
    record = await repo.get(record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return record


@router.patch("/{slug}/records/{record_id}")
async def update_record(
    slug: str,
    record_id: uuid.UUID,
    body: dict[str, Any],
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    repo = await _repo_for(session, ctx.org_id, slug)
    try:
        record = await repo.update(record_id, body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return record


@router.delete("/{slug}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    slug: str,
    record_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    repo = await _repo_for(session, ctx.org_id, slug)
    if not await repo.delete(record_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
