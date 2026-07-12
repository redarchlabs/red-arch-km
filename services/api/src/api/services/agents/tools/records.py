"""Record tools — let an agent read and write custom-entity records.

These are the tools that make the agent org *operational*: filing and updating
issues in the task tracker, capturing ``research_item`` rows, logging KPIs and
action items — any custom entity in the org's catalog.

They reuse ``build_record_repo`` + ``dispatch_inline_workflows`` — the exact code
path the first-party UI and the public ``/api/v1`` API use — so agent writes get
identical catalog validation, relationship checks, workflow-outbox capture, and
at-least-once inline workflow reactions.

Governance:

* ``list_records`` / ``get_record`` are ``READ`` and ``always_allowed`` — every
  agent (coordinator/advisory/operator) can inspect the org's data.
* ``create_record`` / ``update_record`` are ``WRITE`` — the kind-gate restricts
  them to *operators*, and the authority engine additionally requires the tool in
  ``grants.tools`` plus ``grants.records_write``. They are **not** side-effecting
  (an internal write, not an external egress), so under a high-touch org they
  still run without approval — only outbound actions are gated.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from api.repositories.dynamic_entity import EntityRecordError
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec
from api.services.entity_records_helpers import build_record_repo, dispatch_inline_workflows

# Keep tool results compact for the model: never return more than this many rows.
_MAX_LIST = 50


def _entity_slug(args: dict[str, Any]) -> str:
    return str(args.get("entity") or "").strip()


def _parse_id(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _list_records(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    slug = _entity_slug(args)
    if not slug:
        return {"error": "entity (slug) is required"}
    try:
        repo, _definition = await build_record_repo(ctx.session, ctx.org_id, slug)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    search = args.get("search")
    limit = min(int(args.get("limit") or 20), _MAX_LIST)
    try:
        items, next_cursor = await repo.list(
            search=str(search) if search else None,
            limit=limit,
            order_by=args.get("order_by"),
            order_dir=str(args.get("order_dir") or "desc"),
        )
    except EntityRecordError as exc:
        return {"error": str(exc)}
    return {"entity": slug, "count": len(items), "records": items, "has_more": next_cursor is not None}


async def _get_record(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    slug = _entity_slug(args)
    record_id = _parse_id(args.get("id"))
    if not slug or record_id is None:
        return {"error": "entity (slug) and a valid id are required"}
    try:
        repo, _definition = await build_record_repo(ctx.session, ctx.org_id, slug)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    record = await repo.get(record_id)
    if record is None:
        return {"error": "record not found"}
    return {"entity": slug, "record": record}


async def _create_record(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    slug = _entity_slug(args)
    values = args.get("values")
    if not slug or not isinstance(values, dict):
        return {"error": "entity (slug) and a values object are required"}
    try:
        repo, _definition = await build_record_repo(ctx.session, ctx.org_id, slug)
        created = await repo.create(values)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    except EntityRecordError as exc:
        return {"error": str(exc)}
    await dispatch_inline_workflows(ctx.session, ctx.org_id, repo.last_change_event, ctx.settings)
    return {"entity": slug, "id": created.get("id"), "record": created}


async def _update_record(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    slug = _entity_slug(args)
    record_id = _parse_id(args.get("id"))
    values = args.get("values")
    if not slug or record_id is None or not isinstance(values, dict):
        return {"error": "entity (slug), a valid id, and a values object are required"}
    try:
        repo, _definition = await build_record_repo(ctx.session, ctx.org_id, slug)
        updated = await repo.update(record_id, values)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    except EntityRecordError as exc:
        return {"error": str(exc)}
    if updated is None:
        return {"error": "record not found"}
    await dispatch_inline_workflows(ctx.session, ctx.org_id, repo.last_change_event, ctx.settings)
    return {"entity": slug, "id": updated.get("id"), "record": updated}


_ENTITY_PROP = {"type": "string", "description": "Entity definition slug, e.g. 'issue' or 'research_item'."}

LIST_RECORDS = ToolSpec(
    name="list_records",
    description=(
        "List records of a custom entity (newest first). Use this to read the task tracker "
        "(entity 'issue'), prior research ('research_item'), KPIs, and any other org data "
        "before acting."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity": _ENTITY_PROP,
            "search": {"type": "string", "description": "Optional case-insensitive substring match across text fields."},
            "order_by": {"type": "string", "description": "Field slug to sort by (default created_at)."},
            "order_dir": {"type": "string", "enum": ["asc", "desc"], "description": "Sort direction (default desc)."},
            "limit": {"type": "integer", "description": f"Max rows to return (<= {_MAX_LIST})."},
        },
        "required": ["entity"],
    },
    category=Category.READ,
    handler=_list_records,
    always_allowed=True,
)

GET_RECORD = ToolSpec(
    name="get_record",
    description="Fetch a single record of a custom entity by its id.",
    parameters={
        "type": "object",
        "properties": {"entity": _ENTITY_PROP, "id": {"type": "string", "description": "Record id (uuid)."}},
        "required": ["entity", "id"],
    },
    category=Category.READ,
    handler=_get_record,
    always_allowed=True,
)

CREATE_RECORD = ToolSpec(
    name="create_record",
    description=(
        "Create a record of a custom entity. 'values' maps field slugs to values (validated "
        "against the entity catalog); relationships are set by passing the related record's id "
        "under the relationship slug (e.g. 'project': '<id>'). Use this to file an issue, capture "
        "a research_item, or log any org data. Internal write — no human approval required."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity": _ENTITY_PROP,
            "values": {"type": "object", "description": "field-slug -> value (and relationship-slug -> related id)."},
        },
        "required": ["entity", "values"],
    },
    category=Category.WRITE,
    handler=_create_record,
)

UPDATE_RECORD = ToolSpec(
    name="update_record",
    description=(
        "Update fields on an existing record. 'values' is a partial map of field slugs to new "
        "values (only the fields you pass change). Use this to move an issue's status, reassign "
        "it, or complete a research_item. Internal write — no human approval required."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity": _ENTITY_PROP,
            "id": {"type": "string", "description": "Record id (uuid) to update."},
            "values": {"type": "object", "description": "Partial field-slug -> new value map."},
        },
        "required": ["entity", "id", "values"],
    },
    category=Category.WRITE,
    handler=_update_record,
)
