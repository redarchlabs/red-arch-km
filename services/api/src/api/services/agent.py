"""In-API AI agent with OpenAI tool-calling.

Runs the agent loop inside the API process (which already has auth, org context,
and all repositories), so tools mutate the tenant DB with the caller's
permissions. Tools are thin wrappers over the same services the REST endpoints
use. Destructive operations (drop/delete) are intentionally NOT exposed — those
stay in the UI behind explicit confirmation.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import OutboxWriter
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.services.brain_client import BrainAPIClient
from api.services.entity_service import EntityError, EntityService
from api.services.workflow.service import WorkflowError, WorkflowService

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8

_SYSTEM_PROMPT = (
    "You are the configuration assistant for a knowledge-management platform. "
    "You help users model their data as custom entities and automate them with workflows. "
    "Use the provided tools to inspect and change the workspace — do not invent APIs. "
    "When a user asks to 'create a customer entity' or similar, call create_entity with sensible "
    "fields. For questions about existing documents, use search_knowledge_base. "
    "When creating a workflow, fully wire it in one call: set the trigger `operations` from the "
    "user's intent (e.g. 'when a customer is created' -> operations [\"create\"]) and pass the "
    "requested steps as `actions` (e.g. a create_record action targeting the entity to populate). "
    "It is saved as an unpublished draft — tell the user to open it in the builder to review, test, "
    "then Publish. Slugs must be lowercase snake_case. Be concise and friendly."
)

# OpenAI tool (function) schemas.
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Answer a question from the org's ingested documents (RAG).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_entities",
            "description": "List the custom entity types defined in this workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_schema",
            "description": "Get the fields and relationships of one entity by slug.",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_entity",
            "description": "Create a new custom entity type with typed fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string", "description": "lowercase snake_case; derived from name if omitted"},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "slug": {"type": "string"},
                                "field_type": {
                                    "type": "string",
                                    "enum": [
                                        "text", "long_text", "integer", "bigint", "numeric",
                                        "boolean", "date", "timestamptz", "uuid", "json", "picklist",
                                    ],
                                },
                                "picklist_options": {"type": "array", "items": {"type": "string"}},
                                "is_required": {"type": "boolean"},
                                "is_unique": {"type": "boolean"},
                            },
                            "required": ["name", "field_type"],
                        },
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_entity_field",
            "description": "Add a field to an existing entity (added optional).",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_slug": {"type": "string"},
                    "name": {"type": "string"},
                    "field_type": {"type": "string"},
                    "picklist_options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["entity_slug", "name", "field_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_relationship",
            "description": "Create a relationship from one entity to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_slug": {"type": "string"},
                    "target_slug": {"type": "string"},
                    "name": {"type": "string"},
                    "cardinality": {
                        "type": "string",
                        "enum": ["one_to_one", "one_to_many", "many_to_one", "many_to_many"],
                    },
                },
                "required": ["source_slug", "target_slug", "name", "cardinality"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_record",
            "description": "Create a record in an entity. values is keyed by field slug.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_slug": {"type": "string"},
                    "values": {"type": "object"},
                },
                "required": ["entity_slug", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflows",
            "description": "List workflows in this workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_workflow",
            "description": (
                "Create a workflow that fires on record changes to an entity, fully wired with a "
                "trigger and action steps. Saved as an unpublished draft for the user to review "
                "and publish."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entity_slug": {
                        "type": "string",
                        "description": "The entity whose record changes fire the workflow.",
                    },
                    "description": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "description": (
                            "Which record changes fire the workflow. Infer from the request: "
                            "'when X is created' -> [\"create\"]. Defaults to all three if omitted."
                        ),
                        "items": {"type": "string", "enum": ["create", "update", "delete"]},
                    },
                    "field_filter": {
                        "type": "array",
                        "description": (
                            "Optional. Only fire on an update when one of these field slugs "
                            "changes. Ignored for create/delete."
                        ),
                        "items": {"type": "string"},
                    },
                    "actions": {
                        "type": "array",
                        "description": (
                            "Ordered steps to run when the trigger fires. Omit to create just the "
                            "trigger for the user to fill in."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["create_record", "update_record_field", "log"],
                                },
                                "target_slug": {
                                    "type": "string",
                                    "description": "create_record: entity to create a record in.",
                                },
                                "values": {
                                    "type": "object",
                                    "description": (
                                        "create_record: target field slug -> value. A value is "
                                        "either a literal, or a reference to a field on the "
                                        'triggering record via the envelope {"$ref": '
                                        '"after.<field_slug>"} (also "before.<field_slug>") — use '
                                        "this to copy a value from the record that fired the "
                                        "workflow into the new record."
                                    ),
                                },
                                "field": {
                                    "type": "string",
                                    "description": "update_record_field: field slug on the triggering record.",
                                },
                                "value": {"description": "update_record_field: the new value."},
                                "message": {"type": "string", "description": "log: message to record."},
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["name", "entity_slug"],
            },
        },
    },
]

# Action types the assistant is allowed to wire. send_webhook is intentionally
# excluded (its host allow-list / SSRF guard belongs behind explicit UI setup).
_ASSISTANT_ACTION_TYPES = frozenset({"create_record", "update_record_field", "log"})


def _build_workflow_definition(
    operations: list[str], field_filter: list[str], actions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Assemble a stored graph: a trigger followed by a linear chain of action
    nodes. Matches the shape the builder's ``toReactFlow`` renders and
    ``evaluate_graph`` executes."""
    nodes: list[dict[str, Any]] = [
        {
            "id": "trigger",
            "type": "trigger",
            "position": {"x": 240, "y": 40},
            "data": {"operations": operations, "field_filter": field_filter},
        }
    ]
    edges: list[dict[str, Any]] = []
    prev_id = "trigger"
    for index, action in enumerate(actions):
        node_id = f"action_{index}_{uuid.uuid4().hex[:6]}"
        nodes.append(
            {
                "id": node_id,
                "type": "action",
                "position": {"x": 240, "y": 40 + 180 * (index + 1)},
                "data": {"action_type": action["action_type"], "config": action["config"]},
            }
        )
        edges.append(
            {
                "id": f"e_{uuid.uuid4().hex[:6]}",
                "source": prev_id,
                "target": node_id,
                "source_handle": None,
            }
        )
        prev_id = node_id
    return {"schema_version": 1, "nodes": nodes, "edges": edges}


def _slugify(name: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return s if re.match(r"^[a-z]", s) else f"e_{s}"


async def apply_tenant_scope(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Set the tenant GUC on a session, matching the original agent session role.

    IMPORTANT: unlike ``get_tenant_db``, this deliberately does NOT
    ``SET LOCAL ROLE app_user``. The config-assistant's tools include
    ``create_entity`` / ``add_entity_field``, which run physical DDL
    (``CREATE TABLE``/``ALTER TABLE`` on the ``ce_*`` tables) — see
    ``entity_service`` ("Runs on the privileged get_db session"). ``app_user``
    lacks ``CREATE`` on ``public`` and would fail those tools, and the original
    router already ran on the privileged ``get_db`` session (no role drop, no
    RLS). We preserve that exactly. Tenant isolation is enforced by the
    org-bound repositories (every query carries ``self._org_id``), unchanged by
    this refactor. Setting the GUC (transaction-local) is defence-in-depth for
    any trigger/read that consults ``app.current_tenant_id``.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(org_id)},
    )


class AgentService:
    """Config-assistant agent.

    Finding 2 fix: the tool-calling loop must NOT pin a pooled DB connection
    across LLM round-trips (a long turn could exhaust the pool, and all tool
    writes would be one all-or-nothing transaction). So this service holds a
    session *factory*, not a live session, and opens a fresh short-lived
    session PER tool invocation on the same privileged role the original
    ``get_db``-backed router used (see ``apply_tenant_scope`` for why RLS/
    app_user is intentionally not used here), committing per tool. Between tools
    (during LLM network calls) no DB connection is held.
    """

    def __init__(
        self,
        org_id: uuid.UUID,
        settings: Settings,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        org_openai_key: str | None = None,
    ) -> None:
        self._org_id = org_id
        self._settings = settings
        self._session_factory = session_factory
        key = org_openai_key or settings.openai_api_key.get_secret_value()
        self._client = AsyncOpenAI(api_key=key) if key else None

    @asynccontextmanager
    async def _tenant_session(self) -> AsyncGenerator[AsyncSession]:
        """Open a fresh RLS-scoped session for a single tool invocation.

        The caller commits on success; on exception the session context manager
        rolls back the (uncommitted) transaction on close, so a failing tool
        never corrupts or reverts a prior tool's already-committed write.
        """
        async with self._session_factory() as session:
            await apply_tenant_scope(session, self._org_id)
            yield session

    async def run_stream(self, history: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any]]:
        """Yield agent events: delta / tool_call / tool_result / done / error."""
        if self._client is None:
            yield {"type": "error", "error": "No OpenAI API key configured for this org."}
            return

        messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}, *history]
        try:
            for _ in range(MAX_ITERATIONS):
                response = await self._client.chat.completions.create(
                    model=self._settings.openai_model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=TOOLS,  # type: ignore[arg-type]
                    tool_choice="auto",
                )
                message = response.choices[0].message
                if not message.tool_calls:
                    if message.content:
                        yield {"type": "delta", "content": message.content}
                    yield {"type": "done"}
                    return

                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                            }
                            for tc in message.tool_calls
                        ],
                    }
                )
                for tc in message.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    yield {"type": "tool_call", "name": name, "arguments": args}
                    result = await self._dispatch(name, args)
                    yield {"type": "tool_result", "name": name, "result": result}
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)}
                    )
            yield {"type": "delta", "content": "I've reached the step limit for this request."}
            yield {"type": "done"}
        except Exception as exc:  # noqa: BLE001 - surface any agent/LLM failure to the UI
            logger.exception("agent run failed")
            yield {"type": "error", "error": str(exc)}

    async def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool in its OWN short-lived transaction (never raises).

        Each tool gets a fresh tenant-scoped session that is committed here on
        success. A failure rolls back only this tool's writes — prior tools in
        the same LLM turn have already committed independently.
        """
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool: {name}"}
        try:
            async with self._tenant_session() as session:
                result = await handler(session, args)
                await session.commit()
                return result
        except (EntityError, WorkflowError) as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            return {"error": str(exc)}

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    async def _tool_search_knowledge_base(
        self, _session: AsyncSession, args: dict[str, Any]
    ) -> dict[str, Any]:
        # No DB access — talks to brain-api. The (unused) session keeps the
        # dispatch signature uniform across tools.
        client = BrainAPIClient(self._settings)
        result = await client.vector_chat(tenant_id=str(self._org_id), query=args["query"])
        return {"answer": result.get("answer") or result}

    async def _tool_list_entities(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        defs, _ = await EntityDefinitionRepository(session, self._org_id).list_all()
        return {"entities": [{"name": d.name, "slug": d.slug} for d in defs]}

    async def _tool_get_entity_schema(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        definition = await EntityDefinitionRepository(session, self._org_id).get_by_slug(args["slug"])
        if definition is None:
            return {"error": "entity not found"}
        fields = await EntityFieldRepository(session, self._org_id).list_for_definition(definition.id)
        rels = await EntityRelationshipRepository(session, self._org_id).list_for_source(definition.id)
        return {
            "name": definition.name,
            "slug": definition.slug,
            "fields": [{"name": f.name, "slug": f.slug, "type": f.field_type} for f in fields],
            "relationships": [{"name": r.name, "cardinality": r.cardinality} for r in rels],
        }

    async def _tool_create_entity(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        fields = [
            EntityFieldCreate(
                name=f["name"],
                slug=f.get("slug") or _slugify(f["name"]),
                field_type=f["field_type"],
                picklist_options=f.get("picklist_options", []),
                is_required=f.get("is_required", False),
                is_unique=f.get("is_unique", False),
            )
            for f in args.get("fields", [])
        ]
        body = EntityDefinitionCreate(
            name=args["name"], slug=args.get("slug") or _slugify(args["name"]), fields=fields
        )
        definition = await EntityService(session, self._org_id).create_definition(body)
        return {"created": {"name": definition.name, "slug": definition.slug}}

    async def _tool_add_entity_field(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        definition = await EntityDefinitionRepository(session, self._org_id).get_by_slug(args["entity_slug"])
        if definition is None:
            return {"error": "entity not found"}
        field = await EntityService(session, self._org_id).add_field(
            definition.id,
            EntityFieldCreate(
                name=args["name"],
                slug=_slugify(args["name"]),
                field_type=args["field_type"],
                picklist_options=args.get("picklist_options", []),
            ),
        )
        return {"added_field": {"name": field.name, "slug": field.slug}}

    async def _tool_create_relationship(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        repo = EntityDefinitionRepository(session, self._org_id)
        source = await repo.get_by_slug(args["source_slug"])
        target = await repo.get_by_slug(args["target_slug"])
        if source is None or target is None:
            return {"error": "source or target entity not found"}
        rel = await EntityService(session, self._org_id).create_relationship(
            source.id,
            EntityRelationshipCreate(
                name=args["name"],
                slug=_slugify(args["name"]),
                cardinality=args["cardinality"],
                target_definition_id=target.id,
            ),
        )
        return {"created_relationship": {"name": rel.name, "cardinality": rel.cardinality}}

    async def _tool_create_record(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        definition = await EntityDefinitionRepository(session, self._org_id).get_by_slug(args["entity_slug"])
        if definition is None:
            return {"error": "entity not found"}
        fields = await EntityFieldRepository(session, self._org_id).list_for_definition(definition.id)
        rels = await EntityRelationshipRepository(session, self._org_id).list_for_source(definition.id)
        repo = DynamicEntityRepository(
            session, self._org_id, definition, fields, rels, outbox=OutboxWriter(session)
        )
        record = await repo.create(args.get("values", {}))
        return {"created_record_id": str(record["id"])}

    async def _tool_list_workflows(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository

        items = await WorkflowRepository(session, self._org_id).list_all()
        return {"workflows": [{"name": w.name, "enabled": w.enabled} for w in items]}

    async def _tool_create_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        entity_repo = EntityDefinitionRepository(session, self._org_id)
        definition = await entity_repo.get_by_slug(args["entity_slug"])
        if definition is None:
            return {"error": "entity not found"}

        operations = args.get("operations") or ["create", "update", "delete"]
        field_filter = args.get("field_filter") or []

        # Validate + normalise the requested actions into node configs before
        # creating anything, so a bad action never leaves a half-built workflow.
        action_nodes: list[dict[str, Any]] = []
        for spec in args.get("actions") or []:
            action_type = spec.get("type")
            if action_type not in _ASSISTANT_ACTION_TYPES:
                return {"error": f"unsupported action type: {action_type!r}"}
            if action_type == "create_record":
                target_slug = spec.get("target_slug")
                if not target_slug:
                    return {"error": "create_record action requires target_slug"}
                if await entity_repo.get_by_slug(target_slug) is None:
                    return {"error": f"target entity not found: {target_slug!r}"}
                config: dict[str, Any] = {"target_slug": target_slug, "values": spec.get("values", {})}
            elif action_type == "update_record_field":
                field_slug = spec.get("field")
                if not field_slug:
                    return {"error": "update_record_field action requires field"}
                config = {"field": field_slug, "value": spec.get("value")}
            else:  # log
                config = {"message": spec.get("message", "")}
            action_nodes.append({"action_type": action_type, "config": config})

        service = WorkflowService(session, self._org_id)
        wf = await service.create_workflow(
            name=args["name"], entity_definition_id=definition.id, description=args.get("description")
        )
        graph = _build_workflow_definition(operations, field_filter, action_nodes)
        version = await service.save_draft(wf.id, graph)
        return {
            "created_workflow": {"id": str(wf.id), "name": wf.name, "fires_on": definition.slug},
            "trigger": {"operations": operations, "field_filter": field_filter},
            "actions": [n["action_type"] for n in action_nodes],
            "draft_version": version.version_number,
            "note": (
                "Saved as an unpublished draft. Open it in the workflow builder to review and "
                "test, then click Publish to make it live."
            ),
        }
