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
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from openai import AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings

if TYPE_CHECKING:
    from api.auth.dependencies import OrgContext
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

# Tools that mutate workspace configuration (entity/workflow schema) or folders
# and their permissions are org-admin only — the same boundary the REST API
# enforces (folder routes use require_org_admin; the config assistant was
# admin-gated at the endpoint). Non-admin callers get the document + read tools
# only, so the agent can never do more than the user could do in the UI.
_ADMIN_ONLY_TOOLS = frozenset(
    {
        "create_entity",
        "add_entity_field",
        "create_relationship",
        "create_record",
        "create_workflow",
        "create_folder",
        "update_folder",
    }
)

# Document/folder tools act as the calling user and therefore require the
# request's OrgContext to be present (always true in production; absent only in
# unit tests that exercise the config tools in isolation).
_USER_CONTEXT_TOOLS = frozenset(
    {
        "list_folders",
        "list_documents",
        "get_document",
        "list_permission_dimensions",
        "create_document",
        "update_document",
        "update_document_content",
        "create_folder",
        "update_folder",
    }
)


def _parse_uuid(value: Any) -> uuid.UUID | None:
    """Best-effort UUID parse for LLM-supplied args; None on missing/invalid."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# Reused by the folder/document permission-setting tools. A permission config is
# a list of groups (OR'd); within a group every named dimension must match.
_PERMISSION_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": (
        "Permission groups (OR'd together). Each entry is one group the user must fully match. "
        "Keys are dimension NAMES from list_permission_dimensions; any subset of "
        "region/department/role/group (an omitted dimension means 'any'). Empty/omitted = no "
        "restriction (public within the org)."
    ),
    "items": {
        "type": "object",
        "properties": {
            "region": {"type": "string"},
            "department": {"type": "string"},
            "role": {"type": "string"},
            "group": {"type": "string"},
        },
    },
}

_SYSTEM_PROMPT = (
    "You are the assistant for a knowledge-management platform. You help users find, create, and "
    "organize their knowledge base, and model their data. Use the provided tools — do not invent APIs.\n"
    "Documents & folders: browse with list_folders / list_documents / get_document; add notes with "
    "create_document; rewrite a document's body with update_document_content (ALWAYS get_document "
    "first to read the current text, then send the COMPLETE new body — it replaces the old one); "
    "rename/move/re-permission with update_document; organize with create_folder / update_folder. "
    "For questions about existing document contents, use search_knowledge_base.\n"
    "Permissions: you act with the CURRENT USER's permissions — you can only see and change what "
    "they could themselves. Creating or changing folders and setting any permissions require "
    "organization-admin rights; if the user lacks them the tool will refuse, so relay that plainly. "
    "To set permissions, first call list_permission_dimensions for valid region/department/role/group "
    "names, then pass permission groups. You CANNOT delete anything — direct users to the UI for "
    "deletions.\n"
    "Data modeling: help model custom entities and automate them with workflows. For 'create a "
    "customer entity', call create_entity with sensible fields. When creating a workflow, fully wire "
    "it in one call: set the trigger `operations` from intent (e.g. 'when a customer is created' -> "
    '["create"]) and pass steps as `actions`. It saves as an unpublished draft — tell the user to open '
    "it in the builder to review, test, then Publish.\n"
    "Slugs must be lowercase snake_case. Be concise and friendly."
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
    # ---- Knowledge base: documents & folders (act as the calling user) ----
    {
        "type": "function",
        "function": {
            "name": "list_folders",
            "description": "List folders the current user can see (their permissions apply).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List documents the user can see, optionally scoped to one folder.",
            "parameters": {
                "type": "object",
                "properties": {"folder_id": {"type": "string", "description": "optional folder UUID to scope to"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": "Read a document's metadata and its text content (for Markdown/text documents).",
            "parameters": {
                "type": "object",
                "properties": {"document_id": {"type": "string"}},
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_permission_dimensions",
            "description": (
                "List the org's permission dimension names (regions, departments, roles, groups) — "
                "use these names when setting folder/document permissions."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": (
                "Create a document (e.g. a Markdown note) in a folder the user can see. "
                "Pass `text` for inline content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string", "description": "Markdown/plain-text body (optional)."},
                    "description": {"type": "string"},
                    "folder_id": {"type": "string", "description": "Target folder UUID (omit for unfiled)."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_document",
            "description": (
                "Update a document's metadata: title, description, move it to another folder "
                "(folder_id), or set its viewer/contributor permissions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "folder_id": {"type": "string", "description": "Move to this folder UUID."},
                    "viewer_permissions_config": _PERMISSION_CONFIG_SCHEMA,
                    "contributor_permissions_config": _PERMISSION_CONFIG_SCHEMA,
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_document_content",
            "description": (
                "Replace a document's ENTIRE text body and re-index it. Call get_document first to "
                "read the current content, then send the complete new text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "text": {"type": "string", "description": "The complete new body (replaces the old)."},
                },
                "required": ["document_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Create a folder (organization admins only). Optionally set viewer/contributor permissions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Folder name (must not contain '.')."},
                    "parent_id": {"type": "string", "description": "Parent folder UUID (omit for a top-level folder)."},
                    "description": {"type": "string"},
                    "viewer_permissions_config": _PERMISSION_CONFIG_SCHEMA,
                    "contributor_permissions_config": _PERMISSION_CONFIG_SCHEMA,
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_folder",
            "description": (
                "Rename or move a folder, or set its viewer/contributor permissions (organization "
                "admins only). Permission changes propagate to documents and subfolders that inherit it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "parent_id": {"type": "string", "description": "New parent folder UUID."},
                    "viewer_permissions_config": _PERMISSION_CONFIG_SCHEMA,
                    "contributor_permissions_config": _PERMISSION_CONFIG_SCHEMA,
                },
                "required": ["folder_id"],
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
        org_context: OrgContext | None = None,
    ) -> None:
        self._org_id = org_id
        self._settings = settings
        self._session_factory = session_factory
        # The caller's request context. Document/folder tools act as this user
        # and enforce their permissions; None (unit tests exercising config
        # tools) is treated as admin, preserving the prior admin-only behaviour.
        self._ctx = org_context
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
        if name in _USER_CONTEXT_TOOLS and self._ctx is None:
            return {"error": "This tool requires an authenticated user context."}
        if name in _ADMIN_ONLY_TOOLS and not self._is_admin():
            return {"error": "This action requires organization-admin permissions, which you don't have."}
        try:
            async with self._tenant_session() as session:
                result = await handler(session, args)
                await session.commit()
                return result
        except HTTPException as exc:
            # Reused REST handlers raise HTTPException for validation/permission
            # errors (e.g. a folder_id that doesn't exist). Surface the message.
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return {"error": detail}
        except ValidationError as exc:
            return {"error": f"invalid arguments: {exc.errors()[0].get('msg', str(exc))}"}
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

    # ------------------------------------------------------------------ #
    # Permission helpers — documents & folders act as the calling user
    # ------------------------------------------------------------------ #
    def _is_admin(self) -> bool:
        # No context (legacy/tests) → treat as admin, preserving the prior
        # admin-only endpoint behaviour. Production always supplies OrgContext.
        return self._ctx.is_org_admin if self._ctx is not None else True

    async def _visible_folder_ids(self, session: AsyncSession) -> set[uuid.UUID] | None:
        """Folder ids the caller may see, or None meaning 'all' (admin)."""
        if self._is_admin():
            return None
        from api.models.org import Org
        from api.repositories.folder import FolderRepository
        from api.services.permission_config import calculate_user_masks_from_membership

        org = await session.get(Org, self._org_id)
        masks = (
            calculate_user_masks_from_membership(self._ctx.membership, org.permission_number)
            if org is not None
            else []
        )
        folders, _ = await FolderRepository(session, self._org_id).list_visible_to_masks(user_masks=masks)
        return {f.id for f in folders}

    async def _can_see_document(self, session: AsyncSession, doc_id: uuid.UUID) -> bool:
        from api.repositories.document import DocumentRepository

        doc = await DocumentRepository(session, self._org_id).get(doc_id)
        if doc is None:
            return False
        visible = await self._visible_folder_ids(session)
        if visible is None:  # admin
            return True
        if doc.folder_id is None:  # unfiled docs are admin-only (mirrors list rules)
            return False
        return doc.folder_id in visible

    async def _folder_visibility_error(self, session: AsyncSession, folder_id: uuid.UUID | None) -> str | None:
        """Error message if the caller cannot see ``folder_id`` (None = root, allowed)."""
        if folder_id is None:
            return None
        visible = await self._visible_folder_ids(session)
        if visible is not None and folder_id not in visible:
            return "That folder does not exist or is not visible to you."
        return None

    # ------------------------------------------------------------------ #
    # Document & folder tools — thin wrappers over the REST handlers, so the
    # business logic and (for lists) the visibility filtering are reused verbatim
    # with the caller's OrgContext. The admin boundary for folder/permission
    # mutations is enforced centrally in _dispatch (_ADMIN_ONLY_TOOLS).
    # ------------------------------------------------------------------ #
    async def _tool_list_folders(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import folders as folders_routes
        from api.schemas.common import PaginationParams

        page = await folders_routes.list_folders(
            ctx=self._ctx, session=session, pagination=PaginationParams(page=1, page_size=200)
        )
        return {"folders": [{"id": str(f.id), "name": f.name, "path": f.dot_path} for f in page.items]}

    async def _tool_list_documents(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import documents as documents_routes
        from api.schemas.common import PaginationParams

        page = await documents_routes.list_documents(
            ctx=self._ctx,
            session=session,
            pagination=PaginationParams(page=1, page_size=200),
            folder_id=_parse_uuid(args.get("folder_id")),
        )
        return {
            "documents": [
                {"id": str(d.id), "title": d.title, "folder_id": str(d.folder_id) if d.folder_id else None}
                for d in page.items
            ]
        }

    async def _tool_get_document(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import documents as documents_routes

        doc_id = _parse_uuid(args.get("document_id"))
        if doc_id is None:
            return {"error": "document_id is required"}
        if not await self._can_see_document(session, doc_id):
            return {"error": "Document not found or not visible to you."}
        meta = await documents_routes.get_document(document_id=doc_id, ctx=self._ctx, session=session)
        content = await documents_routes.get_document_content(
            document_id=doc_id, ctx=self._ctx, session=session, settings=self._settings
        )
        return {
            "id": str(meta.id),
            "title": meta.title,
            "folder_id": str(meta.folder_id) if meta.folder_id else None,
            "content": content.get("content"),
            "content_kind": content.get("kind"),
        }

    async def _tool_list_permission_dimensions(
        self, session: AsyncSession, _args: dict[str, Any]
    ) -> dict[str, Any]:
        from sqlalchemy import select

        from api.models.org import Department, Group, Region, Role

        out: dict[str, list[str]] = {}
        for key, model in (("regions", Region), ("departments", Department), ("roles", Role), ("groups", Group)):
            rows = (await session.execute(select(model.name).where(model.org_id == self._org_id))).scalars().all()
            out[key] = list(rows)
        return out

    async def _tool_create_document(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import documents as documents_routes
        from api.schemas.document import DocumentCreate

        title = args.get("title")
        if not title:
            return {"error": "title is required"}
        folder_id = _parse_uuid(args.get("folder_id"))
        vis_err = await self._folder_visibility_error(session, folder_id)
        if vis_err:
            return {"error": vis_err}
        body = DocumentCreate(
            title=title, text=args.get("text"), description=args.get("description"), folder_id=folder_id
        )
        result = await documents_routes.create_document(body=body, ctx=self._ctx, session=session)
        return {"created_document": {"id": str(result.id), "title": result.title}}

    async def _tool_update_document(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import documents as documents_routes
        from api.schemas.document import DocumentUpdate

        doc_id = _parse_uuid(args.get("document_id"))
        if doc_id is None:
            return {"error": "document_id is required"}
        if not await self._can_see_document(session, doc_id):
            return {"error": "Document not found or not visible to you."}
        provided: dict[str, Any] = {
            k: args[k]
            for k in ("title", "description", "viewer_permissions_config", "contributor_permissions_config")
            if k in args
        }
        if "folder_id" in args:
            target = _parse_uuid(args.get("folder_id"))
            vis_err = await self._folder_visibility_error(session, target)
            if vis_err:
                return {"error": vis_err}
            provided["folder_id"] = target
        body = DocumentUpdate(**provided)
        result = await documents_routes.update_document(
            document_id=doc_id, body=body, ctx=self._ctx, session=session
        )
        return {"updated_document": {"id": str(result.id), "title": result.title}}

    async def _tool_update_document_content(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import documents as documents_routes
        from api.schemas.document import DocumentContentUpdate

        doc_id = _parse_uuid(args.get("document_id"))
        if doc_id is None:
            return {"error": "document_id is required"}
        if "text" not in args:
            return {"error": "text is required"}
        if not await self._can_see_document(session, doc_id):
            return {"error": "Document not found or not visible to you."}
        body = DocumentContentUpdate(text=args["text"] or "")
        result = await documents_routes.update_document_content(
            document_id=doc_id, body=body, ctx=self._ctx, session=session, settings=self._settings
        )
        return {
            "updated_document": {"id": str(result.id), "title": result.title, "status": result.processing_status}
        }

    async def _tool_create_folder(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import folders as folders_routes
        from api.schemas.document import FolderCreate

        name = args.get("name")
        if not name:
            return {"error": "name is required"}
        body = FolderCreate(
            name=name,
            parent_id=_parse_uuid(args.get("parent_id")),
            description=args.get("description"),
            viewer_permissions_config=args.get("viewer_permissions_config"),
            contributor_permissions_config=args.get("contributor_permissions_config"),
        )
        result = await folders_routes.create_folder(body=body, ctx=self._ctx, session=session)
        return {"created_folder": {"id": str(result.id), "name": result.name, "path": result.dot_path}}

    async def _tool_update_folder(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.routers import folders as folders_routes
        from api.schemas.document import FolderUpdate

        folder_id = _parse_uuid(args.get("folder_id"))
        if folder_id is None:
            return {"error": "folder_id is required"}
        provided: dict[str, Any] = {
            k: args[k]
            for k in ("name", "description", "viewer_permissions_config", "contributor_permissions_config")
            if k in args
        }
        if "parent_id" in args:
            provided["parent_id"] = _parse_uuid(args.get("parent_id"))
        body = FolderUpdate(**provided)
        result = await folders_routes.update_folder(
            folder_id=folder_id, body=body, ctx=self._ctx, session=session
        )
        return {"updated_folder": {"id": str(result.id), "name": result.name, "path": result.dot_path}}
