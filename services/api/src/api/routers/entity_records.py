"""Custom-entity record CRUD.

Runs under ``get_tenant_db`` (``app_user`` + RLS): the catalog is read to build
a ``DynamicEntityRepository`` for the addressed entity, then records are
created/read/updated/deleted with tenant isolation enforced by the database.
Any org member with access may read/write records (``require_org_access``).

The cursor/filter/repo/inline-dispatch plumbing lives in
``services/entity_records_helpers.py`` so this router and the public ``/api/v1``
record surface share one implementation.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.repositories.dynamic_entity import EntityRecordError
from api.schemas.aggregate import AggregateQuery, AggregateResult
from api.services.entity_records_helpers import (
    build_record_repo,
    decode_cursor,
    dispatch_inline_workflows,
    encode_cursor,
    parse_filters,
    resolve_me_filters,
)

router = APIRouter()


@router.get("/{slug}/records")
async def list_records(
    slug: str,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    filter: Annotated[list[str] | None, Query(max_length=300)] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    order_by: Annotated[str | None, Query(max_length=63)] = None,
    order_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, Any]:
    """Keyset-paginated, filterable, optionally-searched record page.

    Pages by an opaque ``cursor`` (no OFFSET) so it scales to millions of rows;
    ``next_cursor`` is the token for the following page, or ``null`` at the end.
    ``q`` does a case-insensitive substring search across text columns.

    Repeat ``filter=<field>:<op>[:<value>]`` for server-side field filtering, e.g.
    ``?filter=stage:eq:won&filter=amount:gte:50000&filter=closed_at:isnull:false``.
    Operators: ``eq, ne, gt, gte, lt, lte, in`` (comma-separated), ``contains``
    (text), ``isnull``. Cursor pagination applies under any ``order_by``.

    ``order_by`` (a field slug or a base column) + ``order_dir`` override the sort
    — used by the record-list view element (e.g. a "latest record" status board).
    Keyset ``cursor`` pagination applies under any sort and with filters applied.
    """
    repo, definition = await build_record_repo(session, ctx.org_id, slug)
    decoded = decode_cursor(cursor) if cursor else None
    # Resolve any ``@me`` filter to the caller's own record id server-side (e.g. a
    # ``filter=learner:eq:@me`` "my rows" board) — never trusting a client id.
    filters = await resolve_me_filters(session, ctx.org_id, definition, parse_filters(filter or []), ctx.user.email)
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
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AggregateResult:
    """Run a GROUP BY / metric query over an entity — the reporting engine's
    ad-hoc surface (saved reports call the same repo method).

    Body is an :class:`AggregateQuery`: ``group_by`` (optionally date-bucketed),
    ``metrics`` (count/count_distinct/sum/avg/min/max), ``filters`` (the same
    operators as the record list), ``having`` on a metric, ``order_by`` a group
    or metric column, and ``limit``. Runs under the tenant's RLS session.
    """
    repo, _definition = await build_record_repo(session, ctx.org_id, slug)
    try:
        return await repo.aggregate(query)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{slug}/records", status_code=status.HTTP_201_CREATED)
async def create_record(
    slug: str,
    body: dict[str, Any],
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    repo, _definition = await build_record_repo(session, ctx.org_id, slug)
    try:
        created = await repo.create(body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await dispatch_inline_workflows(session, ctx.org_id, repo.last_change_event, settings)
    return created


@router.get("/{slug}/records/{record_id}")
async def get_record(
    slug: str,
    record_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    repo, _definition = await build_record_repo(session, ctx.org_id, slug)
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
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    repo, _definition = await build_record_repo(session, ctx.org_id, slug)
    try:
        record = await repo.update(record_id, body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    await dispatch_inline_workflows(session, ctx.org_id, repo.last_change_event, settings)
    return record


@router.delete("/{slug}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    slug: str,
    record_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    repo, _definition = await build_record_repo(session, ctx.org_id, slug)
    if not await repo.delete(record_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    await dispatch_inline_workflows(session, ctx.org_id, repo.last_change_event, settings)
