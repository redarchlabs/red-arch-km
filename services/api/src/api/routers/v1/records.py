"""``/api/v1/entities/{slug}/records`` — entity record CRUD + aggregation.

Wraps the shared record helpers (``services/entity_records_helpers.py``) so the
public API has identical behaviour to the UI, including keyset pagination, the
``filter=field:op:value`` grammar, and at-least-once inline workflow dispatch on
writes. Reads require ``records:read``; writes require ``records:write``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.config import Settings, get_settings
from api.repositories.dynamic_entity import EntityRecordError
from api.schemas.aggregate import AggregateQuery, AggregateResult
from api.services.entity_records_helpers import (
    ME_FILTER_SENTINEL,
    build_record_repo,
    decode_cursor,
    dispatch_inline_workflows,
    encode_cursor,
    parse_filters,
)

router = APIRouter()


@router.get("/{slug}/records")
async def list_records(
    slug: str,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("records:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    filter: Annotated[list[str] | None, Query(max_length=300)] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    order_by: Annotated[str | None, Query(max_length=63)] = None,
    order_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, Any]:
    """Keyset-paginated, filterable, optionally-searched record page.

    Page with the opaque ``cursor``; ``next_cursor`` is ``null`` at the end.
    Repeat ``filter=<field>:<op>[:<value>]`` (ops: ``eq ne gt gte lt lte in
    contains isnull``). ``q`` is a case-insensitive text search.
    """
    repo, _definition = await build_record_repo(session, principal.org_id, slug)
    decoded = decode_cursor(cursor) if cursor else None
    filters = parse_filters(filter or [])
    # `@me` resolves to the CALLING USER's own record — an API key authenticates an
    # org, not a user, so there's nobody to resolve. Reject it with a clear message
    # rather than letting it fall through to an opaque UUID-coercion error.
    if any(isinstance(value, str) and value == ME_FILTER_SENTINEL for _slug, _op, value in filters):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"the {ME_FILTER_SENTINEL!r} filter is not supported for API-key requests (no calling user)",
        )
    try:
        items, next_cursor = await repo.list(
            filters=filters, search=q, cursor=decoded, limit=limit, order_by=order_by, order_dir=order_dir
        )
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "items": items,
        "next_cursor": encode_cursor(next_cursor) if next_cursor else None,
        "limit": limit,
    }


@router.post("/{slug}/aggregate")
async def aggregate_records(
    slug: str,
    query: AggregateQuery,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("records:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> AggregateResult:
    """Run a GROUP BY / metric aggregation over an entity's records."""
    repo, _definition = await build_record_repo(session, principal.org_id, slug)
    try:
        return await repo.aggregate(query)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{slug}/records/{record_id}")
async def get_record(
    slug: str,
    record_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("records:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> dict[str, Any]:
    """Fetch a single record by id."""
    repo, _definition = await build_record_repo(session, principal.org_id, slug)
    record = await repo.get(record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return record


@router.post("/{slug}/records", status_code=status.HTTP_201_CREATED)
async def create_record(
    slug: str,
    body: dict[str, Any],
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("records:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Create a record. Fires any inline (on-change) workflows before returning."""
    repo, _definition = await build_record_repo(session, principal.org_id, slug)
    try:
        created = await repo.create(body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await dispatch_inline_workflows(session, principal.org_id, repo.last_change_event, settings)
    return created


@router.patch("/{slug}/records/{record_id}")
async def update_record(
    slug: str,
    record_id: uuid.UUID,
    body: dict[str, Any],
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("records:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Partially update a record. Fires inline (on-change) workflows."""
    repo, _definition = await build_record_repo(session, principal.org_id, slug)
    try:
        record = await repo.update(record_id, body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    await dispatch_inline_workflows(session, principal.org_id, repo.last_change_event, settings)
    return record


@router.delete("/{slug}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    slug: str,
    record_id: uuid.UUID,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("records:write"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Delete a record. Fires inline (on-change) workflows."""
    repo, _definition = await build_record_repo(session, principal.org_id, slug)
    if not await repo.delete(record_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    await dispatch_inline_workflows(session, principal.org_id, repo.last_change_event, settings)
