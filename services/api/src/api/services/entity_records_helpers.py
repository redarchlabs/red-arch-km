"""Shared helpers for custom-entity record access.

Extracted from ``routers/entity_records.py`` so the internal (Clerk) router AND
the public ``/api/v1`` (API-key) router build the record repository, encode
cursors, parse filters, and dispatch inline workflows through **one** code path —
guaranteeing the enterprise API has the exact same behaviour (including
at-least-once inline workflow reactions) as the first-party UI.
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
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
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
    FilterClause,
    RecordCursor,
)
from api.repositories.workflow import OutboxWriter, WorkflowRepository
from api.services.workflow.factory import build_dispatch_service

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


def cursor_value_to_json(value: Any) -> Any:
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


def encode_cursor(cursor: RecordCursor) -> str:
    """Opaque, URL-safe token carrying the keyset position and its sort context."""
    payload = {
        "s": cursor.order_slug,
        "d": cursor.order_dir,
        "v": cursor_value_to_json(cursor.order_value),
        "id": str(cursor.id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(token: str) -> RecordCursor:
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        return RecordCursor(
            order_slug=str(data["s"]),
            order_dir=str(data["d"]),
            order_value=data["v"],  # raw JSON scalar; repo re-coerces to the column type
            id=uuid.UUID(str(data["id"])),
        )
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor") from exc


def parse_filters(raw_filters: list[str]) -> list[FilterClause]:
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


async def build_record_repo(
    session: AsyncSession, org_id: uuid.UUID, slug: str
) -> tuple[DynamicEntityRepository, EntityDefinition]:
    """Resolve an entity by slug and build its record repository (404 if unknown).

    The repository is wired with an :class:`OutboxWriter` so record changes land
    in the workflow outbox in the same transaction (at-least-once workflow firing).
    """
    definition = await EntityDefinitionRepository(session, org_id).get_by_slug(slug)
    if definition is None or not definition.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition.id)
    rels = await EntityRelationshipRepository(session, org_id).list_for_source(definition.id)
    repo = DynamicEntityRepository(session, org_id, definition, fields, rels, outbox=OutboxWriter(session))
    return repo, definition


async def dispatch_inline_workflows(
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
        dispatcher = build_dispatch_service(session, settings)
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
