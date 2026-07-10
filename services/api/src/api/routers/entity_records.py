"""Custom-entity record CRUD.

Runs under ``get_tenant_db`` (``app_user`` + RLS): the catalog is read to build
a ``DynamicEntityRepository`` for the addressed entity, then records are
created/read/updated/deleted with tenant isolation enforced by the database.
Any org member with access may read/write records (``require_org_access``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.models.custom_entity import EntityDefinition
from api.models.workflow import WorkflowOutbox
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import (
    FILTER_OPERATORS,
    DynamicEntityRepository,
    EntityRecordError,
    FilterClause,
    RecordCursor,
)
from api.repositories.workflow import OutboxWriter, WorkflowRepository
from api.schemas.aggregate import AggregateQuery, AggregateResult
from api.services.email import EmailSender
from api.services.workflow.dispatcher import WorkflowDispatchService

logger = logging.getLogger(__name__)

# Hard ceiling on how long an inline (run_inline_on_change) dispatch may hold the
# request's pooled DB connection. Inline workflows run synchronously in the
# record-write request; without a budget a slow LLM/RAG step (OpenAI SDK default
# 600s, brain-api 120s) could pin one of a small pool of connections for minutes
# and exhaust it under concurrent writes. On timeout the savepoint rolls back and
# the outbox row (left pending) is completed by the beat sweep instead. Keep inline
# workflows to a few fast steps (say/perform/mood); heavy multi-step LLM work
# belongs on the async beat path.
_INLINE_DISPATCH_BUDGET_SECONDS = 15.0

router = APIRouter()


def _cursor_value_to_json(value: Any) -> Any:
    """Render a sort-key value as a JSON-safe scalar for the cursor token.

    Dates/datetimes → ISO strings; Decimal/UUID → strings; ints/bools/strings/None
    pass through. The repository re-coerces the string form back to the column
    type on the next page.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    return str(value)


def _encode_cursor(cursor: RecordCursor) -> str:
    """Opaque, URL-safe token carrying the keyset position and its sort context."""
    payload = {
        "s": cursor.order_slug,
        "d": cursor.order_dir,
        "v": _cursor_value_to_json(cursor.order_value),
        "id": str(cursor.id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(token: str) -> RecordCursor:
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        return RecordCursor(
            order_slug=str(data["s"]),
            order_dir=str(data["d"]),
            order_value=data["v"],  # raw JSON scalar; repo re-coerces to the column type
            id=uuid.UUID(str(data["id"])),
        )
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor"
        ) from exc


def _parse_filters(raw_filters: list[str]) -> list[FilterClause]:
    """Parse ``filter=<slug>:<op>[:<value>]`` query params into filter clauses.

    ``in`` values are comma-split into a list; ``isnull`` values are read as a
    boolean (``filter=email:isnull`` ⇒ IS NULL, ``:false`` ⇒ IS NOT NULL). A
    malformed clause or unknown operator is a 400.
    """
    clauses: list[FilterClause] = []
    for raw in raw_filters:
        parts = raw.split(":", 2)
        if len(parts) < 2 or not parts[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid filter {raw!r}; expected '<field>:<op>[:<value>]'",
            )
        slug, op = parts[0], parts[1]
        value: Any = parts[2] if len(parts) == 3 else None
        if op not in FILTER_OPERATORS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown filter operator {op!r}; allowed: {sorted(FILTER_OPERATORS)}",
            )
        if op == "in":
            value = value.split(",") if value else []
        elif op == "isnull":
            value = value is None or str(value).strip().casefold() in ("true", "1", "yes", "y")
        clauses.append((slug, op, value))
    return clauses


async def _repo_for(
    session: AsyncSession, org_id: uuid.UUID, slug: str
) -> tuple[DynamicEntityRepository, EntityDefinition]:
    definition = await EntityDefinitionRepository(session, org_id).get_by_slug(slug)
    if definition is None or not definition.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition.id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition.id)
    # Capture record changes into the workflow outbox in the same transaction so
    # workflows fire reliably (at-least-once).
    repo = DynamicEntityRepository(session, org_id, definition, fields, rels, outbox=OutboxWriter(session))
    return repo, definition


async def _dispatch_inline_workflows(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_row: WorkflowOutbox | None,
    settings: Settings,
) -> None:
    """Run any ``run_inline_on_change`` workflows for a just-written change NOW,
    in-request, so a latency-sensitive reaction (e.g. a robot announcing a state
    change) fires without the beat-sweep delay.

    ``event_row`` is the EXACT outbox row this request wrote (``repo.last_change_event``)
    — keying off it avoids re-querying "the row I just wrote", which races a
    concurrent writer on the same record. A cheap EXISTS gate skips the work when
    no workflow opted in. Drives the matching inline workflows in a savepoint; the
    whole body is guarded so a workflow/robot failure is logged and swallowed and the
    record write still commits. The real outbox row is left ``pending`` so the beat
    sweep dedups these runs and still fires any non-inline workflows on the change.

    Semantics: side effects are AT-LEAST-ONCE and fire BEFORE the request commits.
    An inline action's external effect (robot /perform, email) runs inside the
    savepoint; if the savepoint later rolls back (timeout/error after the effect
    fired) or the request's final commit fails, the outbox row stays pending and the
    beat sweep re-runs the workflow — the external effect can repeat. Inline
    workflows should therefore be idempotent-friendly (announcements, not payments)."""
    if event_row is None:
        return
    try:
        wf_repo = WorkflowRepository(session, org_id)
        if not await wf_repo.has_inline_for_entity(event_row.entity_definition_id):
            return
        event = {
            "id": event_row.id,
            "seq": event_row.seq,
            "created_at": event_row.created_at,
            "org_id": org_id,
            "entity_definition_id": event_row.entity_definition_id,
            "entity_table": event_row.entity_table,
            "record_id": event_row.record_id,
            "operation": event_row.operation,
            "before_data": event_row.before_data,
            "after_data": event_row.after_data,
            "origin_run_id": event_row.origin_run_id,
            "source": event_row.source,
        }
        dispatcher = WorkflowDispatchService(
            session,
            webhook_allowlist=tuple(settings.workflow_webhook_allowlist or ()),
            trusted_local_hosts=tuple(settings.workflow_trusted_local_hosts or ()),
            public_base_url=settings.public_base_url,
            email_sender=EmailSender(settings),
            org_encryption_key=settings.org_encryption_key.get_secret_value(),
            settings=settings,
        )
        async with session.begin_nested():
            # Bounded so a slow inline workflow can't pin the request's connection.
            # The timeout almost always lands during an external LLM/HTTP await (the
            # long pole), leaving the DB idle so the savepoint rolls back cleanly.
            await asyncio.wait_for(
                dispatcher.run_inline_for_change(event), timeout=_INLINE_DISPATCH_BUDGET_SECONDS
            )
    except Exception:  # noqa: BLE001 — inline reaction failure must not fail the record write
        # Swallow (this also catches asyncio.wait_for's TimeoutError): the record
        # write must still commit. A timeout/failure leaves the outbox row pending,
        # so the beat sweep completes the workflow instead.
        logger.exception("inline workflow dispatch failed or timed out for org %s", org_id)


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
    repo, _definition = await _repo_for(session, ctx.org_id, slug)
    decoded = _decode_cursor(cursor) if cursor else None
    filters = _parse_filters(filter or [])
    try:
        items, next_cursor = await repo.list(
            filters=filters, search=q, cursor=decoded, limit=limit, order_by=order_by, order_dir=order_dir
        )
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "items": items,
        "next_cursor": _encode_cursor(next_cursor) if next_cursor else None,
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
    repo, _definition = await _repo_for(session, ctx.org_id, slug)
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
    repo, _definition = await _repo_for(session, ctx.org_id, slug)
    try:
        created = await repo.create(body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _dispatch_inline_workflows(session, ctx.org_id, repo.last_change_event, settings)
    return created


@router.get("/{slug}/records/{record_id}")
async def get_record(
    slug: str,
    record_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, Any]:
    repo, _definition = await _repo_for(session, ctx.org_id, slug)
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
    repo, _definition = await _repo_for(session, ctx.org_id, slug)
    try:
        record = await repo.update(record_id, body)
    except EntityRecordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    await _dispatch_inline_workflows(session, ctx.org_id, repo.last_change_event, settings)
    return record


@router.delete("/{slug}/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    slug: str,
    record_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    repo, _definition = await _repo_for(session, ctx.org_id, slug)
    if not await repo.delete(record_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    await _dispatch_inline_workflows(session, ctx.org_id, repo.last_change_event, settings)
