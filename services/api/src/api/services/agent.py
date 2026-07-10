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

# Tool-calling turns allowed per user message. Multi-step authoring goals
# (e.g. create_entity → create_workflow → publish → create_form → validate →
# create_view) legitimately need ~7 sequential turns before any retry, and a
# "build the whole thing" request chains several of those — so the budget is
# generous. When it IS exhausted we still emit an actionable summary (see the
# tool_choice="none" wrap-up in the run loop) rather than dead-ending.
MAX_ITERATIONS = 32

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
        # Intake forms are entirely an org-admin surface in the REST API
        # (every /api/forms route uses require_org_admin), so mirror that here.
        "list_forms",
        "get_form",
        "create_form",
        "update_form",
        "delete_form",
        "validate_form_layout",
        # Views: composable screens (same element tree) — full lifecycle.
        "list_views",
        "get_view",
        "create_view",
        "update_view",
        "delete_view",
        # Workflow listing, authoring, publishing, dry-run testing, monitoring
        # and retry all mirror require_org_admin REST routes (GET /workflows/ is
        # itself admin-only). `run_workflow` is the one exception — it is gated on
        # the workflow's own run_permission via can_run(), NOT on org-admin, so it
        # is deliberately absent here.
        "list_workflows",
        "get_workflow",
        "update_workflow",
        "save_workflow_definition",
        "validate_workflow",
        "publish_workflow",
        "test_workflow",
        "list_workflow_runs",
        "get_workflow_run",
        "retry_workflow_run",
        "complete_workflow_task",
        # Connector credentials + inbound webhook endpoints (mirror the org-admin
        # REST routes under /workflows/connections and /workflows/inbound-endpoints).
        "list_connections",
        "create_connection",
        "delete_connection",
        "list_webhooks",
        "create_webhook",
        "delete_webhook",
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


def _format_validation_errors(exc: ValidationError, limit: int = 6) -> str:
    """Render a Pydantic error as located, actionable text for the model.

    The default first-error `msg` (e.g. bare "Field required") tells the LLM
    nothing about WHICH field of WHICH element is wrong, so it retries blindly.
    We surface `loc: msg` for every error (capped) so the agent can repair the
    exact spot — e.g. ``elements.0.action: Field required``.
    """
    errors = exc.errors()
    parts: list[str] = []
    for err in errors[:limit]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    if len(errors) > limit:
        parts.append(f"(+{len(errors) - limit} more)")
    return "; ".join(parts)


# Tools whose failures have a well-known recovery move. We surface the hint IN
# the error payload (only when a call actually fails) rather than front-loading
# it into the system prompt — progressive disclosure: the model sees the fix
# exactly when it's relevant and pays no tokens for it on the happy path.
_DRY_RUN_HINT: dict[str, str] = {
    "create_form": "Dry-run first with validate_form_layout (pass entity_slug) to get located errors before saving.",
    "update_form": "Dry-run the new tree with validate_form_layout (pass entity_slug) before saving.",
    "create_view": "Dry-run first with validate_form_layout (omit entity_slug for a standalone view) before saving.",
    "update_view": "Dry-run the new tree with validate_form_layout before saving.",
    "save_workflow_definition": "Check the graph with validate_workflow (no save) and fix the returned issues, then resend.",
}


def _attach_recovery_hint(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Add a contextual `hint` to an error result so the model knows the recovery
    move. No-op on success or when a hint is already present."""
    if not isinstance(result, dict) or "error" not in result or "hint" in result:
        return result
    err = str(result["error"]).lower()
    hint: str | None = None
    if "not found" in err:
        # A bad id/slug — point the model at the matching listing tool to verify.
        hint = "Confirm the id/slug exists first with the matching list_/get_ tool (e.g. list_entities, list_workflows, list_forms)."
    elif "invalid arguments" in err or "required" in err or "valid" in err:
        hint = _DRY_RUN_HINT.get(name)
    else:
        hint = _DRY_RUN_HINT.get(name)
    if hint:
        return {**result, "hint": hint}
    return result


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
    "Workflow lifecycle: browse with list_workflows; inspect one fully (versions + the active graph) "
    "with get_workflow. Author/repair a graph with save_workflow_definition — it takes a full BPMN "
    "graph ({nodes, edges}) and is validated before saving as a new draft; if it returns issues, fix "
    "them and resend. Check any graph without saving via validate_workflow. Change name/enabled/"
    "run-permission with update_workflow. Publish a reviewed draft with publish_workflow (blocked if "
    "the graph has validation errors). Dry-run a version with NO side effects via test_workflow before "
    "publishing. Run the published workflow for real with run_workflow (needs run permission on that "
    "workflow; supply a record_id for anything that emails/webhooks). Debug & monitor instances: "
    "list_workflow_runs shows recent runs and their status; get_workflow_run returns a run's steps and "
    "control-flow tokens with per-step output/error; retry_workflow_run re-runs the failed step(s) of a "
    "failed run. When a run is 'waiting' on a human task (a user_task token in get_workflow_run), "
    "complete_workflow_task advances it — pass `variables` for any approval decision the flow branches "
    'on (e.g. {"approved": true}). Authoring/publishing/testing/monitoring are org-admin only; running '
    "honors the workflow's run_permission.\n"
    "Forms & views: a form binds to one entity and is filled internally (Forms UI) or via a public "
    "share link to create/update a record; a view is a composable screen that arranges forms "
    "(form_ref), action buttons and layout, optionally bound to an entity. Both use the SAME v2 "
    "element tree in `config` = {version:2, elements:[...]}. Do NOT guess element shapes — call "
    "describe_form_elements for the full element vocabulary (each type + its required/optional "
    "fields) and get_entity_schema for the field/relationship slugs to bind. Inspect with "
    "list_forms/get_form (or list_views/get_view), build with create_form/create_view, edit with "
    "update_form/update_view (config REPLACES the whole tree — get first, modify, send the complete "
    "tree), remove with delete_form/delete_view. Dry-run any tree with validate_form_layout (pass "
    "entity_slug to also check bindings) before saving; invalid trees are rejected with a precise, "
    "located error to repair from. Org-admin only.\n"
    "End-to-end automation: to collect input and trigger an action (e.g. 'a button that asks for a "
    "statement and posts it to a REST endpoint'), build a workflow whose step performs the action "
    "(call describe_workflow_actions to pick the right action — send_webhook/http_request post to a "
    "URL), then surface it with a form/view button whose action is run_workflow (mapping the "
    "collected fields into the workflow inputs).\n"
    "Slugs must be lowercase snake_case. Be concise and friendly."
)

# A full BPMN 2.0 workflow graph, as authored by save_workflow_definition /
# validated by validate_workflow. Kept deliberately lenient (data is free-form)
# — the server validates structure (schemas/workflow_definition.py) and BPMN
# semantics (services/workflow/validation.py) and returns precise issues, so the
# model can author freely and repair from feedback rather than satisfy a rigid
# schema up front.
_WORKFLOW_DEFINITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "A workflow graph. schema_version 2 uses the BPMN vocabulary below. Node `type` is the BPMN "
        "category; the concrete subtype lives in `data`."
    ),
    "properties": {
        "schema_version": {"type": "integer", "enum": [2], "description": "Use 2 for BPMN graphs."},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Unique, [A-Za-z0-9_-], <=64 chars."},
                    "type": {
                        "type": "string",
                        "enum": ["trigger", "task", "gateway", "event"],
                        "description": (
                            "trigger=start; task=work step; gateway=branch/fork/join; event=intermediate/"
                            "end/boundary catch/throw."
                        ),
                    },
                    "data": {
                        "type": "object",
                        "description": (
                            "Subtype + config. trigger: {operations:[create|update|delete], field_filter:[slug]}. "
                            "task: {task_type: service|send|script|businessRule|user|receive|call|subProcess|"
                            "manual, action_type, config}. gateway: {gateway_type: exclusive|parallel|inclusive|"
                            "event_based, expr, cases}. event: {position: intermediate|end|boundary, event_type: "
                            "timer|message|signal|error|escalation|terminate|none, attached_to (boundary host id)}."
                        ),
                    },
                    "position": {
                        "type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                        "description": "Optional canvas coords; auto-arranged if omitted.",
                    },
                },
                "required": ["id", "type"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "source": {"type": "string", "description": "Source node id."},
                    "target": {"type": "string", "description": "Target node id."},
                    "source_handle": {
                        "type": "string",
                        "description": (
                            "Which branch the edge leaves the source by: true/false (condition), default, "
                            "error (from a boundary error event), case-<id>, or omit for the sole out-edge."
                        ),
                    },
                },
                "required": ["source", "target"],
            },
        },
    },
    "required": ["nodes", "edges"],
}

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
                                        "text",
                                        "long_text",
                                        "integer",
                                        "bigint",
                                        "numeric",
                                        "boolean",
                                        "date",
                                        "timestamptz",
                                        "uuid",
                                        "json",
                                        "picklist",
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
                "trigger and a linear chain of action steps. Saved as an unpublished draft for the "
                "user to review and publish. Call describe_workflow_actions for the action `type`s "
                "and their config keys (e.g. send_webhook to POST to a REST endpoint). For branching/"
                "parallel/timers or anything non-linear, author the full graph with "
                "save_workflow_definition instead."
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
                            "description": (
                                "One step. See describe_workflow_actions for each type's config. "
                                "Config values may reference the triggering record via a "
                                '{"$ref": "after.<slug>"} / "before.<slug>" envelope; send_webhook '
                                "also auto-includes before/after in its POST body."
                            ),
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "create_record",
                                        "update_record_field",
                                        "log",
                                        "send_webhook",
                                        "http_request",
                                        "send_email",
                                        "send_form",
                                    ],
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
                                "url": {
                                    "type": "string",
                                    "description": "send_webhook/http_request: target URL (host must be allow-listed).",
                                },
                                "body": {
                                    "description": "send_webhook/http_request/send_email: optional payload/body.",
                                },
                                "connection": {
                                    "type": "string",
                                    "description": "http_request: optional saved connection name for auth.",
                                },
                                "path": {
                                    "type": "string",
                                    "description": "http_request: path appended to the connection base_url (alternative to url).",
                                },
                                "method": {
                                    "type": "string",
                                    "description": "http_request: HTTP method (default GET).",
                                },
                                "headers": {
                                    "type": "object",
                                    "description": "http_request: optional request headers.",
                                },
                                "to": {
                                    "type": "string",
                                    "description": "send_email: recipient address (or an after.<slug> reference).",
                                },
                                "subject": {"type": "string", "description": "send_email: subject line."},
                                "form_id": {
                                    "type": "string",
                                    "description": "send_form: the form to mint a link for.",
                                },
                                "recipient_field": {
                                    "type": "string",
                                    "description": "send_form: record field slug holding the recipient email.",
                                },
                                "recipient": {
                                    "type": "string",
                                    "description": "send_form: literal recipient email (or after.<slug> reference).",
                                },
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["name", "entity_slug"],
            },
        },
    },
    # ---- Workflow lifecycle: author / run / debug / monitor (org-admin,
    #      except run_workflow which honors the workflow's run_permission) ----
    {
        "type": "function",
        "function": {
            "name": "get_workflow",
            "description": (
                "Get a workflow's full detail: id, bound entity, enabled state, run permission, its "
                "versions (with status), and the active (or latest) graph as {nodes, edges}. Call this "
                "before save_workflow_definition or publish_workflow so you edit/publish the right graph."
            ),
            "parameters": {
                "type": "object",
                "properties": {"workflow_id": {"type": "string"}},
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_workflow",
            "description": (
                "Update a workflow's metadata: name, description, enabled (on/off), and who may run it "
                "(run_permission). Does not touch the graph — use save_workflow_definition for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "enabled": {"type": "boolean", "description": "Whether the workflow fires on its trigger."},
                    "run_permission": {
                        "type": "object",
                        "description": (
                            "Who may manually run it. {mode: 'org_admin'|'any_member'|'roles', role_ids:[uuid], "
                            "group_ids:[uuid]}. org admins can always run."
                        ),
                    },
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_workflow_definition",
            "description": (
                "Save a full BPMN graph as a new DRAFT version of an existing workflow. Call "
                "describe_bpmn_vocabulary for the node/gateway/event shapes first, and prefer "
                "checking the graph with validate_workflow (no save) before this — repeated blind "
                "saves waste steps. The graph is validated on save too; if it has errors nothing is "
                "saved and the issues are returned to fix and resend. Warnings do not block saving. "
                "Publish separately with publish_workflow once reviewed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "definition": _WORKFLOW_DEFINITION_SCHEMA,
                },
                "required": ["workflow_id", "definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_workflow",
            "description": (
                "Validate a workflow graph WITHOUT saving — returns structural + BPMN semantic issues "
                "(errors block publish; warnings are advisory). Pass a `definition` to check a draft you "
                "are composing, or a `workflow_id` to check its active/latest saved graph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "definition": _WORKFLOW_DEFINITION_SCHEMA,
                    "workflow_id": {
                        "type": "string",
                        "description": "Validate this workflow's active/latest graph instead.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_workflow",
            "description": (
                "Publish a draft version, making it the live graph that fires on the trigger and that "
                "run_workflow executes. Refuses if the graph has validation errors. Omit version_id to "
                "publish the latest draft."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "version_id": {
                        "type": "string",
                        "description": "Draft to publish; defaults to the latest draft.",
                    },
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test_workflow",
            "description": (
                "Dry-run a workflow version against sample record data with NO side effects (actions are "
                "simulated, never executed). Returns which conditions matched and each step's simulated "
                "output — use this to debug branching before publishing. Omit version_id to test the "
                "active/latest version."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "version_id": {"type": "string"},
                    "operation": {
                        "type": "string",
                        "enum": ["create", "update", "delete"],
                        "description": "Defaults to update.",
                    },
                    "before": {
                        "type": "object",
                        "description": "Record field values BEFORE the change (field slug -> value).",
                    },
                    "after": {"type": "object", "description": "Record field values AFTER the change."},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_workflow",
            "description": (
                "Run a workflow's PUBLISHED version for REAL (records a run, performs side effects). "
                "Honors the workflow's run permission. For any workflow that emails/webhooks/sends a "
                "form, pass a record_id so it runs against a real record (its data is loaded server-side; "
                "client-supplied before/after is refused for those). Use test_workflow first if unsure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "record_id": {
                        "type": "string",
                        "description": "Id of a real record of the workflow's entity to run against.",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["create", "update", "delete"],
                        "description": "Defaults to update.",
                    },
                    "before": {
                        "type": "object",
                        "description": "Only used (and only for non-side-effecting graphs) when no record_id is given.",
                    },
                    "after": {"type": "object"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflow_runs",
            "description": (
                "List recent runs (instances) of a workflow, newest first — id, status, trigger "
                "operation, timestamps, and error. Use to monitor what a workflow has been doing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "limit": {"type": "integer", "description": "How many runs to return (default 20, max 100)."},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workflow_run",
            "description": (
                "Get one run's full detail for debugging: status/error, every step (node, status, "
                "attempts, output, error) and the control-flow tokens (node, status, wait reason). "
                "Use after list_workflow_runs to see why a run failed or is waiting."
            ),
            "parameters": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_workflow_run",
            "description": (
                "Retry a FAILED run: reactivates its failed step(s) and re-drives the run from where it "
                "died (not a full replay). Returns the new status. Only works on runs that failed on the "
                "token engine; a run with nothing retryable is reported as such."
            ),
            "parameters": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_workflow_task",
            "description": (
                "Complete a human task a run is WAITING on (e.g. an approval) and advance the run. "
                'Provide `variables` for any decision the workflow branches on (e.g. {"approved": true}). '
                "Use get_workflow_run first to see which task node is waiting. Reports 'nothing to "
                "complete' if the run isn't awaiting a task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "node_id": {
                        "type": "string",
                        "description": "Which waiting task node to complete; defaults to the first.",
                    },
                    "variables": {
                        "type": "object",
                        "description": (
                            "Decision values merged into the run's variables so downstream gateways "
                            'can route (e.g. {"approved": true}).'
                        ),
                    },
                    "output": {"type": "object", "description": "Optional data recorded as the task's output."},
                },
                "required": ["run_id"],
            },
        },
    },
    # ---- Intake forms (org-admin) ----
    {
        "type": "function",
        "function": {
            "name": "list_forms",
            "description": "List the intake forms defined in this workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_form",
            "description": (
                "Get a form's full definition: name, slug, bound entity, active state, and its "
                "complete v2 layout tree (`config.elements`). Call this before update_form to see "
                "the exact element shapes, then send a modified tree back."
            ),
            "parameters": {
                "type": "object",
                "properties": {"form_id": {"type": "string"}},
                "required": ["form_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_form",
            "description": (
                "Create a form bound to an entity, using the flexible v2 layout tree. `config` is "
                "`{version:2, elements:[...]}` where each element has a `type`: field (bind an entity "
                "field by slug; supports read_only/required/width/display), label, calculated "
                "(JsonLogic `expression` + optional `target_slug` to persist), button "
                "(submit/run_workflow/link), section (1:1 related record), table (1:M grid with "
                "field + cross-entity related columns), block (repeating 1:M), and layout containers "
                "(tab_group, panel, accordion, columns). Omit `config` for an empty form. Fill via the "
                "Forms UI (internal) or a shared link (public)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entity_slug": {
                        "type": "string",
                        "description": "The entity whose records this form creates/updates.",
                    },
                    "slug": {
                        "type": "string",
                        "description": "URL slug (lowercase snake_case). Defaults from the name if omitted.",
                    },
                    "description": {"type": "string"},
                    "config": {
                        "type": "object",
                        "description": (
                            "The v2 layout tree {version:2, elements:[...]}. Call describe_form_elements "
                            "for the element shapes and get_entity_schema for bindable slugs; dry-run with "
                            "validate_form_layout. Omit for an empty form."
                        ),
                    },
                },
                "required": ["name", "entity_slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_form",
            "description": (
                "Update a form. Pass only what changes. NOTE: `config` REPLACES the entire layout tree "
                "— call get_form first, modify the returned `config`, and send the COMPLETE new tree "
                "(omit `config` to leave the layout untouched). Use is_active to enable/disable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "form_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "is_active": {"type": "boolean"},
                    "config": {
                        "type": "object",
                        "description": (
                            "COMPLETE new v2 layout tree {version:2, elements:[...]} (replaces existing). "
                            "See describe_form_elements; dry-run with validate_form_layout."
                        ),
                    },
                },
                "required": ["form_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_form",
            "description": "Delete a form. Existing share links stop working. Irreversible.",
            "parameters": {
                "type": "object",
                "properties": {"form_id": {"type": "string"}},
                "required": ["form_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_views",
            "description": "List the views (composable screens) in this workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_view",
            "description": "Get a view's full definition incl. its v2 layout tree. Call before update_view.",
            "parameters": {
                "type": "object",
                "properties": {"view_id": {"type": "string"}},
                "required": ["view_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_view",
            "description": (
                "Create a view — a composable screen using the same v2 element tree as forms, plus "
                "`form_ref` widgets that embed a form and buttons that run workflows. Omit "
                "`entity_slug` for a standalone view (only labels/buttons/form_ref/layout allowed); "
                "supply it to bind the view to an entity record (then fields/tables/calculated work too)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string", "description": "URL slug; defaults from name."},
                    "entity_slug": {
                        "type": "string",
                        "description": "Optional entity binding. Omit for a standalone dashboard view.",
                    },
                    "description": {"type": "string"},
                    "config": {
                        "type": "object",
                        "description": (
                            "The v2 layout tree {version:2, elements:[...]}. Call describe_form_elements "
                            "for element shapes (views may also use form_ref); dry-run with "
                            "validate_form_layout."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_view",
            "description": (
                "Update a view. `config` REPLACES the whole layout tree — call get_view first, modify, "
                "and send the complete tree. Use is_active to enable/disable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "view_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "is_active": {"type": "boolean"},
                    "config": {
                        "type": "object",
                        "description": (
                            "COMPLETE new v2 layout tree (replaces existing). See describe_form_elements; "
                            "dry-run with validate_form_layout."
                        ),
                    },
                },
                "required": ["view_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_view",
            "description": "Delete a view. Irreversible.",
            "parameters": {
                "type": "object",
                "properties": {"view_id": {"type": "string"}},
                "required": ["view_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_form_elements",
            "description": (
                "Reference: the v2 form/view element vocabulary — every element `type` with its "
                "required and optional fields (field, label, calculated, button, section, table, "
                "block, tab_group, panel, accordion, columns, and form_ref for views), plus the "
                "button `action` shapes. Call this BEFORE authoring a form/view `config` so you use "
                "the correct shapes; no arguments."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_form_layout",
            "description": (
                "Dry-run a v2 layout tree WITHOUT saving. Returns {valid:true} or {valid:false, "
                "errors:[...]} with located messages to repair from. Pass entity_slug to also verify "
                "that bound field slugs and relationships exist on that entity (omit for a standalone "
                "view — structural check only). Use this to iterate before create_form/update_form/"
                "create_view/update_view."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "The v2 layout tree {version:2, elements:[...]} to check.",
                    },
                    "entity_slug": {
                        "type": "string",
                        "description": "Optional entity to validate field/relationship bindings against.",
                    },
                },
                "required": ["config"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_workflow_actions",
            "description": (
                "Reference: the workflow action catalog — every action `type` (send_webhook, "
                "http_request, send_email, send_form, create_record, update_record_field, log) with "
                "its `config` keys and when to use it. Call this when authoring a workflow (create_"
                "workflow / save_workflow_definition) so you pick the right step — e.g. send_webhook "
                "to POST to a REST endpoint. No arguments."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_bpmn_vocabulary",
            "description": (
                "Reference: the BPMN graph vocabulary for save_workflow_definition — node categories "
                "(trigger/task/gateway/event), task_types, gateway_types and event_types, plus the "
                "edge shape. Call this ONLY when authoring a full non-linear graph (branching, "
                "parallel, timers, boundary events) with save_workflow_definition; for a simple "
                "linear trigger→actions flow use create_workflow + describe_workflow_actions. No "
                "arguments."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_connections",
            "description": "List reusable HTTP connections (for http_request workflow steps). Secrets are never returned.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_connection",
            "description": (
                "Create a reusable HTTP connection an http_request step calls by name (base_url + "
                "optional auth). E.g. a local device bridge: {name:'robot', base_url:'http://localhost:8080', "
                "auth_type:'none'}. For a private/loopback base_url the host must also be in the "
                "server's trusted-local-hosts allowlist (SSRF guard). The secret is stored encrypted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "base_url": {
                        "type": "string",
                        "description": "e.g. https://api.example.com or http://localhost:8080",
                    },
                    "auth_type": {"type": "string", "enum": ["none", "bearer", "api_key", "basic"]},
                    "secret": {
                        "type": "string",
                        "description": "Bearer token / api key / basic password (encrypted at rest).",
                    },
                    "config": {
                        "type": "object",
                        "description": "Non-secret auth config: api-key header name, basic username, static headers.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_connection",
            "description": "Delete an HTTP connection by id. http_request steps referencing it will then fail.",
            "parameters": {
                "type": "object",
                "properties": {"connection_id": {"type": "string"}},
                "required": ["connection_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_webhooks",
            "description": (
                "List inbound webhook endpoints — public URLs that START a workflow when POSTed to. "
                "Shows whether each requires a signature; never returns the token or secret."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_webhook",
            "description": (
                "Create an inbound webhook endpoint bound to a workflow: an external system POSTs to "
                "the returned URL and the workflow runs immediately with the POST body as input "
                "(reference it via {{after.<field>}}). Returns the URL, one-time token, and an HMAC "
                "signing secret (shown ONCE) — the caller must sign each request, so a leaked URL "
                "alone can't trigger it. Use for 'when <external event> happens, run <workflow>'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "workflow_id": {
                        "type": "string",
                        "description": "The workflow this endpoint starts (must be published to run).",
                    },
                },
                "required": ["name", "workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_webhook",
            "description": "Delete an inbound webhook endpoint by id. Its URL stops working immediately.",
            "parameters": {
                "type": "object",
                "properties": {"endpoint_id": {"type": "string"}},
                "required": ["endpoint_id"],
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
                "Create a document (e.g. a Markdown note) in a folder the user can see. Pass `text` for inline content."
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
_ASSISTANT_ACTION_TYPES = frozenset(
    {
        "create_record",
        "update_record_field",
        "log",
        # Outbound / messaging actions — the runtime has handlers for these
        # (services/workflow/actions.py); the simple linear builder passes their
        # config straight to a task node, so e.g. "POST the statement to a REST
        # endpoint" is one create_workflow call, not a hand-authored BPMN graph.
        "send_webhook",
        "http_request",
        "send_email",
        "send_form",
        # Read-only knowledge-base lookup (hybrid RAG). Answers a question from the
        # org's docs; pair with a node ``capture`` + ``{{ vars.<key> }}`` in a later
        # step (token-engine graph) to speak/store the answer.
        "knowledge_search",
        # Small-LLM condensation of text into one short spoken line (e.g. shrink a
        # RAG answer before a robot /say). Read-only.
        "summarize",
    }
)


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
        node_data: dict[str, Any] = {"action_type": action["action_type"], "config": action["config"]}
        if action.get("capture"):
            node_data["capture"] = action["capture"]
        nodes.append(
            {
                "id": node_id,
                "type": "action",
                "position": {"x": 240, "y": 40 + 180 * (index + 1)},
                "data": node_data,
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


def _iso(value: Any) -> str | None:
    """Serialize a datetime (or None) to an ISO string for tool JSON output."""
    return value.isoformat() if value is not None else None


_EMPTY_GRAPH: dict[str, Any] = {"schema_version": 2, "nodes": [], "edges": []}

# The trigger operations a manual run / dry-run may claim (mirrors ManualRunRequest).
_MANUAL_RUN_OPERATIONS = frozenset({"create", "update", "delete"})


def _version_summary(version: Any) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "published_at": _iso(version.published_at),
    }


def _active_or_latest(versions: list[Any], active_version_id: Any) -> Any | None:
    """The version an author is looking at: the active (published) one if set,
    else the newest (``list_for_workflow`` returns version_number-desc)."""
    active = next((v for v in versions if v.id == active_version_id), None)
    return active or (versions[0] if versions else None)


def _run_summary(run: Any) -> dict[str, Any]:
    """Compact run row for list_workflow_runs / the header of get_workflow_run."""
    return {
        "id": str(run.id),
        "status": run.status,
        "operation": run.trigger_operation,
        "record_id": str(run.record_id) if run.record_id else None,
        "conditions_matched": bool(run.conditions_matched),
        "error": run.error,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
    }


def _step_summary(step: Any) -> dict[str, Any]:
    return {
        "node_id": step.node_id,
        "action_type": step.action_type,
        "status": step.status,
        "attempts": step.attempts,
        "output": step.output,
        "error": step.error,
    }


def _token_summary(token: Any) -> dict[str, Any]:
    return {
        "node_id": token.node_id,
        "status": token.status,
        "wait_kind": token.wait_kind,
        "resume_at": _iso(token.resume_at),
    }


def _prepare_authored_definition(raw: Any) -> dict[str, Any]:
    """Normalize an LLM-authored graph into a storable definition.

    Fills the ergonomic gaps a model tends to leave — schema_version, missing
    node positions (a simple vertical cascade so the graph is legible when the
    user opens it), and edge ids — WITHOUT touching semantics. Returns a plain
    dict; structural + BPMN validation happens afterwards on the caller side.
    """
    if not isinstance(raw, dict):
        return {"schema_version": 2, "nodes": [], "edges": []}
    raw_nodes = raw.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    raw_edges = raw.get("edges")
    edges = raw_edges if isinstance(raw_edges, list) else []
    out_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node = dict(node)
        pos = node.get("position")
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            node["position"] = {"x": 240.0, "y": 40.0 + 140.0 * index}
        out_nodes.append(node)
    out_edges: list[dict[str, Any]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        edge = dict(edge)
        if not edge.get("id"):
            edge["id"] = f"e_{index}_{uuid.uuid4().hex[:6]}"
        out_edges.append(edge)
    version = raw.get("schema_version")
    # `bool` is an `int` subclass — exclude it so a stray `true` doesn't store as 1.
    valid_version = isinstance(version, int) and not isinstance(version, bool) and version >= 1
    return {
        "schema_version": version if valid_version else 2,
        "nodes": out_nodes,
        "edges": out_edges,
    }


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
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
            # Budget exhausted mid-task. Instead of dead-ending, force ONE
            # tool-free turn so the model reports what it accomplished and the
            # exact remaining step(s) — a resumable handoff, not a wall.
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "You have hit the tool-step limit for this turn. Do NOT request any more "
                        "tools. In 1-3 sentences tell the user what you have already created/changed "
                        "(name the resources) and the precise next step(s) remaining, and invite them "
                        "to say 'continue' so you can finish."
                    ),
                }
            )
            try:
                wrap_up = await self._client.chat.completions.create(
                    model=self._settings.openai_model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=TOOLS,  # type: ignore[arg-type]
                    tool_choice="none",
                )
                summary = wrap_up.choices[0].message.content
            except Exception:  # noqa: BLE001 - fall back to a static message if the wrap-up call fails
                logger.exception("step-limit wrap-up call failed")
                summary = None
            yield {
                "type": "delta",
                "content": summary
                or (
                    "I've reached the step limit for this request. Ask me to continue and I'll pick "
                    "up where I left off."
                ),
            }
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
                # Handlers may themselves return {"error": ...} (e.g. "entity not
                # found"); attach a recovery hint to those too.
                return _attach_recovery_hint(name, result)
        except HTTPException as exc:
            # Reused REST handlers raise HTTPException for validation/permission
            # errors (e.g. a folder_id that doesn't exist). Surface the message.
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return _attach_recovery_hint(name, {"error": detail})
        except ValidationError as exc:
            return _attach_recovery_hint(name, {"error": f"invalid arguments — {_format_validation_errors(exc)}"})
        except (EntityError, WorkflowError) as exc:
            return _attach_recovery_hint(name, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            return _attach_recovery_hint(name, {"error": str(exc)})

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    async def _tool_search_knowledge_base(self, _session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        # No DB access — talks to brain-api. The (unused) session keeps the
        # dispatch signature uniform across tools.
        client = BrainAPIClient(self._settings)
        result = await client.vector_chat(tenant_id=str(self._org_id), query=args["query"])
        return {"answer": result.get("answer") or result}

    async def _tool_list_entities(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        defs, _ = await EntityDefinitionRepository(session, self._org_id).list_all()
        return {"entities": [{"name": d.name, "slug": d.slug} for d in defs]}

    async def _tool_get_entity_schema(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        entity_repo = EntityDefinitionRepository(session, self._org_id)
        definition = await entity_repo.get_by_slug(args["slug"])
        if definition is None:
            return {"error": "entity not found"}
        fields = await EntityFieldRepository(session, self._org_id).list_for_definition(definition.id)
        rel_repo = EntityRelationshipRepository(session, self._org_id)
        outgoing = await rel_repo.list_for_source(definition.id)
        incoming = await rel_repo.list_targeting(definition.id)
        defs, _ = await entity_repo.list_all()
        slug_by_id = {d.id: d.slug for d in defs}

        # Both directions can back a form section: an OUTGOING to-one FK is a 1:1
        # inline/modal section; an INCOMING relationship (another entity points at
        # this one) is a 1:M child "table" section. Expose the related entity's
        # slug so the model can look up that entity's fields to build the section.
        outgoing_ids = {r.id for r in outgoing}
        relationships = [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "cardinality": r.cardinality,
                "direction": "outgoing",
                "related_entity_slug": slug_by_id.get(r.target_definition_id),
            }
            for r in outgoing
        ] + [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "cardinality": r.cardinality,
                "direction": "incoming",
                "related_entity_slug": slug_by_id.get(r.source_definition_id),
            }
            for r in incoming
            if r.id not in outgoing_ids  # a self-referential rel appears in both lists
        ]
        return {
            "name": definition.name,
            "slug": definition.slug,
            "fields": [{"name": f.name, "slug": f.slug, "type": f.field_type} for f in fields],
            "relationships": relationships,
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
        body = EntityDefinitionCreate(name=args["name"], slug=args.get("slug") or _slugify(args["name"]), fields=fields)
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
        repo = DynamicEntityRepository(session, self._org_id, definition, fields, rels, outbox=OutboxWriter(session))
        record = await repo.create(args.get("values", {}))
        return {"created_record_id": str(record["id"])}

    async def _tool_list_workflows(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository

        items = await WorkflowRepository(session, self._org_id).list_all()
        # Include the id (needed by every other workflow tool) and whether a
        # published version exists (only those are runnable / fire on triggers).
        return {
            "workflows": [
                {
                    "id": str(w.id),
                    "name": w.name,
                    "enabled": w.enabled,
                    "has_published_version": w.active_version_id is not None,
                }
                for w in items
            ]
        }

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
            elif action_type == "send_webhook":
                url = spec.get("url")
                if not url:
                    return {"error": "send_webhook action requires url"}
                config = {"url": url}
                if spec.get("body") is not None:
                    config["body"] = spec["body"]
            elif action_type == "http_request":
                if not spec.get("url") and not spec.get("path"):
                    return {"error": "http_request action requires url (or connection + path)"}
                config = {
                    k: spec[k]
                    for k in ("connection", "url", "path", "method", "headers", "body")
                    if spec.get(k) is not None
                }
            elif action_type == "knowledge_search":
                query = spec.get("query")
                if not query:
                    return {"error": "knowledge_search action requires query"}
                config = {"query": query}
            elif action_type == "summarize":
                stext = spec.get("text")
                if not stext:
                    return {"error": "summarize action requires text"}
                config = {"text": stext}
                for k in ("question", "max_words", "instruction", "model"):
                    if spec.get(k) is not None:
                        config[k] = spec[k]
            elif action_type == "send_email":
                to = spec.get("to")
                if not to:
                    return {"error": "send_email action requires to"}
                config = {"to": to, "subject": spec.get("subject", ""), "body": spec.get("body", "")}
            elif action_type == "send_form":
                form_id = spec.get("form_id")
                if not form_id:
                    return {"error": "send_form action requires form_id"}
                config = {"form_id": form_id}
                for k in ("recipient_field", "recipient"):
                    if spec.get(k) is not None:
                        config[k] = spec[k]
            else:  # log
                config = {"message": spec.get("message", "")}
            node_spec: dict[str, Any] = {"action_type": action_type, "config": config}
            # Optional: publish this step's output as a run variable (``vars.<key>``)
            # for a later step to template. Only piped through on the token engine.
            if spec.get("capture"):
                node_spec["capture"] = str(spec["capture"])
            action_nodes.append(node_spec)

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
            "definition_summary": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
            "note": (
                "Saved as an unpublished draft. Publish it with publish_workflow (or open the "
                "builder to review/test first). To let a user trigger it from a screen, add a "
                "button with action {kind:'run_workflow', workflow_id} to a form or view."
            ),
        }

    # ------------------------------------------------------------------ #
    # Workflow lifecycle tools — author / run / debug / monitor. Authoring,
    # publishing, dry-run testing and monitoring mirror the org-admin REST
    # routes (gated in _dispatch via _ADMIN_ONLY_TOOLS); run_workflow honors the
    # workflow's own run_permission via can_run().
    # ------------------------------------------------------------------ #
    async def _tool_get_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository, WorkflowVersionRepository

        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        wf = await WorkflowRepository(session, self._org_id).get(wf_id)
        if wf is None:
            return {"error": "workflow not found"}
        versions = await WorkflowVersionRepository(session, self._org_id).list_for_workflow(wf.id)
        graph_version = _active_or_latest(versions, wf.active_version_id)
        entity = await EntityDefinitionRepository(session, self._org_id).get(wf.entity_definition_id)
        return {
            "workflow": {
                "id": str(wf.id),
                "name": wf.name,
                "description": wf.description,
                "enabled": wf.enabled,
                "entity": entity.slug if entity is not None else None,
                "run_permission": wf.run_permission,
                "active_version_id": str(wf.active_version_id) if wf.active_version_id else None,
                "versions": [_version_summary(v) for v in versions],
                "definition": graph_version.definition if graph_version is not None else _EMPTY_GRAPH,
                "definition_version": graph_version.version_number if graph_version is not None else None,
            }
        }

    async def _tool_update_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository

        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        repo = WorkflowRepository(session, self._org_id)
        wf = await repo.get(wf_id)
        if wf is None:
            return {"error": "workflow not found"}
        fields = ("name", "description", "enabled", "run_permission")
        updates: dict[str, Any] = {k: args[k] for k in fields if k in args}
        await repo.update(wf, **updates)
        return {
            "updated_workflow": {
                "id": str(wf.id),
                "name": wf.name,
                "enabled": wf.enabled,
                "run_permission": wf.run_permission,
            }
        }

    async def _tool_save_workflow_definition(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.services.workflow.validation import has_errors, validate_definition

        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        service = WorkflowService(session, self._org_id)
        try:
            await service.get_workflow(wf_id)  # 404s via WorkflowError if absent
        except WorkflowError as exc:
            return {"error": str(exc)}
        definition = _prepare_authored_definition(args.get("definition"))
        # Validate BEFORE mutating: a bad graph must never leave a half-saved draft.
        issues = validate_definition(definition)
        if has_errors(issues):
            return {
                "error": "the workflow graph has validation errors and was not saved",
                "issues": [i.as_dict() for i in issues],
            }
        version = await service.save_draft(wf_id, definition)
        return {
            "saved_draft": {
                "workflow_id": str(wf_id),
                "version_id": str(version.id),
                "version_number": version.version_number,
            },
            "warnings": [i.as_dict() for i in issues if i.severity == "warning"],
            "note": "Saved as a draft. Review/test it, then publish_workflow to make it live.",
        }

    async def _tool_validate_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository, WorkflowVersionRepository
        from api.services.workflow.validation import has_errors, validate_definition

        if args.get("definition") is not None:
            definition = _prepare_authored_definition(args.get("definition"))
        else:
            wf_id = _parse_uuid(args.get("workflow_id"))
            if wf_id is None:
                return {"error": "pass a definition to validate, or a workflow_id to validate its saved graph"}
            wf = await WorkflowRepository(session, self._org_id).get(wf_id)
            if wf is None:
                return {"error": "workflow not found"}
            versions = await WorkflowVersionRepository(session, self._org_id).list_for_workflow(wf.id)
            graph_version = _active_or_latest(versions, wf.active_version_id)
            if graph_version is None:
                return {"error": "workflow has no versions to validate"}
            definition = graph_version.definition
        issues = validate_definition(definition)
        return {
            "valid": not has_errors(issues),
            "errors": [i.as_dict() for i in issues if i.severity == "error"],
            "warnings": [i.as_dict() for i in issues if i.severity == "warning"],
        }

    async def _tool_publish_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowVersionRepository
        from api.services.workflow.validation import has_errors, validate_definition

        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        service = WorkflowService(session, self._org_id)
        try:
            await service.get_workflow(wf_id)
        except WorkflowError as exc:
            return {"error": str(exc)}
        ver_repo = WorkflowVersionRepository(session, self._org_id)
        version_id = _parse_uuid(args.get("version_id"))
        if version_id is None:
            drafts = [v for v in await ver_repo.list_for_workflow(wf_id) if v.status == "draft"]
            if not drafts:
                return {"error": "no draft version to publish"}
            version_id = drafts[0].id  # list is newest-first
        version = await ver_repo.get(version_id)
        if version is None or version.workflow_id != wf_id:
            return {"error": "version not found"}
        # Refuse to publish a graph with hard errors (validation.errors block publish).
        issues = validate_definition(version.definition)
        if has_errors(issues):
            return {
                "error": "the graph has validation errors and cannot be published",
                "issues": [i.as_dict() for i in issues if i.severity == "error"],
            }
        try:
            published = await service.publish(wf_id, version_id)
        except WorkflowError as exc:
            return {"error": str(exc)}
        return {
            "published": {
                "workflow_id": str(wf_id),
                "version_id": str(published.id),
                "version_number": published.version_number,
            },
            "note": "This version is now live and will run on its trigger.",
        }

    async def _tool_test_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository, WorkflowVersionRepository

        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        operation = args.get("operation", "update")
        if operation not in _MANUAL_RUN_OPERATIONS:
            return {"error": f"operation must be one of {sorted(_MANUAL_RUN_OPERATIONS)}"}
        wf = await WorkflowRepository(session, self._org_id).get(wf_id)
        if wf is None:
            return {"error": "workflow not found"}
        ver_repo = WorkflowVersionRepository(session, self._org_id)
        version_id = _parse_uuid(args.get("version_id"))
        if version_id is None:
            versions = await ver_repo.list_for_workflow(wf.id)
            graph_version = _active_or_latest(versions, wf.active_version_id)
            if graph_version is None:
                return {"error": "workflow has no versions to test"}
            version_id = graph_version.id
        try:
            result = await WorkflowService(session, self._org_id).test_version(
                version_id,
                operation=operation,
                before=args.get("before"),
                after=args.get("after"),
            )
        except WorkflowError as exc:
            return {"error": str(exc)}
        return {"test_result": result}

    async def _tool_run_workflow(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRepository, WorkflowVersionRepository
        from api.services.email import EmailSender
        from api.services.workflow.actions import SIDE_EFFECTING_ACTIONS
        from api.services.workflow.dispatcher import WorkflowDispatchService
        from api.services.workflow.permissions import can_run

        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        operation = args.get("operation", "update")
        if operation not in _MANUAL_RUN_OPERATIONS:
            return {"error": f"operation must be one of {sorted(_MANUAL_RUN_OPERATIONS)}"}
        wf = await WorkflowRepository(session, self._org_id).get(wf_id)
        if wf is None:
            return {"error": "workflow not found"}
        # run_workflow is NOT admin-gated: honor the workflow's run_permission.
        # A None OrgContext (unit tests) is treated as admin, matching _is_admin().
        if self._ctx is not None and not can_run(self._ctx, wf.run_permission):
            return {"error": "You don't have permission to run this workflow."}
        if wf.active_version_id is None:
            return {"error": "workflow has no published version"}
        version = await WorkflowVersionRepository(session, self._org_id).get(wf.active_version_id)
        if version is None or version.status != "published":
            return {"error": "workflow has no published version"}

        dispatcher = WorkflowDispatchService(
            session,
            webhook_allowlist=tuple(self._settings.workflow_webhook_allowlist or ()),
            trusted_local_hosts=tuple(self._settings.workflow_trusted_local_hosts or ()),
            public_base_url=self._settings.public_base_url,
            email_sender=EmailSender(self._settings),
            org_encryption_key=self._settings.org_encryption_key.get_secret_value(),
            settings=self._settings,
        )
        # SECURITY (mirrors POST /workflows/{id}/run): never trust client record
        # data. With a record_id, load real before/after server-side; without one,
        # a side-effecting run is still allowed but its record context is NULLED,
        # so only the workflow's own author-defined config can leave the org.
        before: dict[str, Any] | None
        after: dict[str, Any] | None
        record_id = _parse_uuid(args.get("record_id"))
        if record_id is not None:
            record = await dispatcher.load_trigger_record(self._org_id, wf.entity_definition_id, record_id)
            if record is None:
                return {"error": "record not found for this workflow's entity"}
            before = after = record
        else:
            # Collect action_type from BOTH legacy `action` nodes and v2 `task`
            # nodes (a send task carries the same action_type) so a side-effecting
            # step in either vocabulary drops any client-supplied before/after.
            action_types = {
                node.get("data", {}).get("action_type")
                for node in version.definition.get("nodes", [])
                if node.get("type") in ("action", "task")
            }
            if action_types & SIDE_EFFECTING_ACTIONS:
                before, after = None, None  # never trust client data outbound
            else:
                before, after = args.get("before"), args.get("after")

        actor = self._ctx.user.profile_id if self._ctx is not None else None
        run, executed = await dispatcher.run_version_manually(
            self._org_id,
            wf,
            version,
            operation=operation,
            record_id=record_id,
            before=before,
            after=after,
            actor_user_id=actor,
        )
        return {
            "run": {
                "id": str(run.id),
                "status": run.status,
                "conditions_matched": bool(run.conditions_matched),
                "actions_executed": executed,
                "error": run.error,
            }
        }

    async def _tool_list_workflow_runs(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        wf_id = _parse_uuid(args.get("workflow_id"))
        if wf_id is None:
            return {"error": "workflow_id is required"}
        limit = max(1, min(int(args.get("limit") or 20), 100))
        try:
            runs = await WorkflowService(session, self._org_id).runs(wf_id, limit=limit)
        except WorkflowError as exc:
            return {"error": str(exc)}
        return {"runs": [_run_summary(r) for r in runs]}

    async def _tool_get_workflow_run(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRunRepository, WorkflowTokenRepository

        run_id = _parse_uuid(args.get("run_id"))
        if run_id is None:
            return {"error": "run_id is required"}
        run_repo = WorkflowRunRepository(session, self._org_id)
        run = await run_repo.get_by_id(run_id)
        if run is None:
            return {"error": "run not found"}
        steps = await run_repo.steps_for_run(run.id)
        tokens = await WorkflowTokenRepository(session, self._org_id).list_for_run(run.id)
        return {
            "run": _run_summary(run),
            "steps": [_step_summary(s) for s in steps],
            "tokens": [_token_summary(t) for t in tokens],
        }

    async def _tool_retry_workflow_run(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRunRepository, WorkflowVersionRepository
        from api.services.email import EmailSender
        from api.services.workflow.engine import TokenEngine

        run_id = _parse_uuid(args.get("run_id"))
        if run_id is None:
            return {"error": "run_id is required"}
        run_repo = WorkflowRunRepository(session, self._org_id)
        run = await run_repo.get_by_id(run_id)
        if run is None:
            return {"error": "run not found"}
        if run.status != "failed":
            return {"error": f"only failed runs can be retried (this run is {run.status!r})"}
        version = await WorkflowVersionRepository(session, self._org_id).get(run.workflow_version_id)
        if version is None:
            return {"error": "the run's workflow version no longer exists"}
        engine = TokenEngine(
            session,
            webhook_allowlist=tuple(self._settings.workflow_webhook_allowlist or ()),
            trusted_local_hosts=tuple(self._settings.workflow_trusted_local_hosts or ()),
            public_base_url=self._settings.public_base_url,
            email_sender=EmailSender(self._settings),
            org_encryption_key=self._settings.org_encryption_key.get_secret_value(),
            settings=self._settings,
        )
        result = await engine.retry_run(run, version.definition)
        if result.get("reactivated", 0) == 0:
            return {"error": "nothing to retry on this run (no failed token — it may predate the token engine)"}
        refreshed = await run_repo.get_by_id(run_id)
        return {
            "retried_run": {
                "id": str(run_id),
                "reactivated": result.get("reactivated", 0),
                "status": refreshed.status if refreshed is not None else run.status,
                "error": refreshed.error if refreshed is not None else run.error,
            }
        }

    async def _tool_complete_workflow_task(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowRunRepository, WorkflowVersionRepository
        from api.services.email import EmailSender
        from api.services.workflow.engine import TokenEngine

        run_id = _parse_uuid(args.get("run_id"))
        if run_id is None:
            return {"error": "run_id is required"}
        run_repo = WorkflowRunRepository(session, self._org_id)
        run = await run_repo.get_by_id(run_id)
        if run is None:
            return {"error": "run not found"}
        if run.status not in ("waiting", "running"):
            return {"error": f"this run is {run.status!r}, not awaiting a task"}
        version = await WorkflowVersionRepository(session, self._org_id).get(run.workflow_version_id)
        if version is None:
            return {"error": "the run's workflow version no longer exists"}
        engine = TokenEngine(
            session,
            webhook_allowlist=tuple(self._settings.workflow_webhook_allowlist or ()),
            trusted_local_hosts=tuple(self._settings.workflow_trusted_local_hosts or ()),
            public_base_url=self._settings.public_base_url,
            email_sender=EmailSender(self._settings),
            org_encryption_key=self._settings.org_encryption_key.get_secret_value(),
            settings=self._settings,
        )
        variables = args.get("variables") if isinstance(args.get("variables"), dict) else None
        output = args.get("output") if isinstance(args.get("output"), dict) else None
        node_id = args.get("node_id") if isinstance(args.get("node_id"), str) else None
        signaled = await engine.signal_token(run, node_id=node_id, variables=variables, output=output)
        if not signaled:
            return {"error": "no human task is waiting on this run (nothing to complete)"}
        await engine.drive_run(run)
        refreshed = await run_repo.get_by_id(run_id)
        return {
            "completed_task": {
                "run_id": str(run_id),
                "node_id": node_id,
                "status": refreshed.status if refreshed is not None else run.status,
            }
        }

    # ------------------------------------------------------------------ #
    # Intake-form tools (org-admin) — mirror the /api/forms REST surface,
    # delegating to FormService. FormError is turned into a friendly message.
    # ------------------------------------------------------------------ #
    async def _tool_list_forms(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.services.form_service import FormService

        forms = await FormService(session, self._org_id).list_forms()
        return {"forms": [{"id": str(f.id), "name": f.name, "slug": f.slug, "is_active": f.is_active} for f in forms]}

    async def _tool_get_form(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.form import FormRead
        from api.services.form_service import FormError, FormService

        form_id = _parse_uuid(args.get("form_id"))
        if form_id is None:
            return {"error": "form_id is required"}
        try:
            form = await FormService(session, self._org_id).get_form(form_id)
        except FormError as exc:
            return {"error": str(exc)}
        return {"form": FormRead.model_validate(form).model_dump(mode="json")}

    async def _tool_create_form(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.form import FormConfig, FormCreate
        from api.services.form_service import FormError, FormService

        name = args.get("name")
        entity_slug = args.get("entity_slug")
        if not name:
            return {"error": "name is required"}
        if not entity_slug:
            return {"error": "entity_slug is required"}
        definition = await EntityDefinitionRepository(session, self._org_id).get_by_slug(entity_slug)
        if definition is None:
            return {"error": f"entity not found: {entity_slug!r}"}
        config = FormConfig.model_validate(args.get("config") or {})
        body = FormCreate(
            name=name,
            slug=args.get("slug") or _slugify(name),
            entity_definition_id=definition.id,
            description=args.get("description"),
            config=config,
        )
        try:
            form = await FormService(session, self._org_id).create_form(body)
        except FormError as exc:
            return {"error": str(exc)}
        return {
            "created_form": {
                "id": str(form.id),
                "name": form.name,
                "slug": form.slug,
                "entity": definition.slug,
            },
            "note": "Open the Forms UI to generate a shareable link, or fill it at /forms/{id}/fill.",
        }

    async def _tool_update_form(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.form import FormConfig, FormUpdate
        from api.services.form_service import FormError, FormService

        form_id = _parse_uuid(args.get("form_id"))
        if form_id is None:
            return {"error": "form_id is required"}
        provided: dict[str, Any] = {k: args[k] for k in ("name", "description", "is_active") if k in args}
        # `config` is a full-layout replacement; only touch it when supplied.
        if "config" in args:
            provided["config"] = FormConfig.model_validate(args["config"] or {})
        body = FormUpdate(**provided)
        try:
            form = await FormService(session, self._org_id).update_form(form_id, body)
        except FormError as exc:
            return {"error": str(exc)}
        return {
            "updated_form": {
                "id": str(form.id),
                "name": form.name,
                "slug": form.slug,
                "is_active": form.is_active,
            }
        }

    async def _tool_delete_form(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.services.form_service import FormError, FormService

        form_id = _parse_uuid(args.get("form_id"))
        if form_id is None:
            return {"error": "form_id is required"}
        try:
            await FormService(session, self._org_id).delete_form(form_id)
        except FormError as exc:
            return {"error": str(exc)}
        return {"deleted_form": str(form_id)}

    async def _tool_describe_form_elements(self, _session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        """On-demand reference for the v2 element vocabulary (no DB access).

        Kept out of the tool schemas / system prompt so the model pulls it only
        when authoring — progressive disclosure instead of front-loading ~5k
        tokens of layout schema on every request.
        """
        return {
            "config_shape": "{version: 2, elements: [ <element>, ... ]}",
            "notes": (
                "Every element is an object with a `type` plus the fields below. Containers nest "
                "arbitrarily. Bind by entity-field slug / relationship_id from get_entity_schema. "
                "Widths: full|half|third|quarter."
            ),
            "elements": {
                "field": {
                    "required": ["type", "slug"],
                    "optional": [
                        "label",
                        "required",
                        "read_only",
                        "width",
                        "help_text",
                        "placeholder",
                        "display (dropdown|radio)",
                    ],
                    "use": "Bind one entity field by its slug.",
                },
                "label": {
                    "required": ["type", "text", "variant (heading|subheading|paragraph|divider)"],
                    "use": "Static presentational text / divider.",
                },
                "calculated": {
                    "required": ["type", "expression", "result_type (text|integer|numeric|boolean|date|timestamptz)"],
                    "optional": ["label", "target_slug", "help_text", "width"],
                    "use": (
                        "JsonLogic `expression` over field slugs (date ops: today, now, date_add, "
                        "date_diff). Set target_slug to PERSIST the result to an entity field; omit "
                        "for display-only. The server recomputes persisted values authoritatively."
                    ),
                },
                "input": {
                    "required": ["type", "key", "control (text|textarea|number|slider|toggle|select)"],
                    "optional": [
                        "label",
                        "placeholder",
                        "help_text",
                        "default",
                        "required",
                        "width",
                        "min",
                        "max",
                        "step (number|slider)",
                        "options:[{value,label?}] (select)",
                    ],
                    "use": (
                        "A STANDALONE input NOT bound to an entity field — its value lives in form "
                        "state under `key`. Reference it from a button's inputs or a calculated "
                        'expression as {"var": "<key>"}. Use for sliders/toggles/free-text in a '
                        "standalone view that feed a workflow run. Never persisted to a record."
                    ),
                },
                "live_value": {
                    "required": ["type", "url"],
                    "optional": ["label", "json_pointer (dot path e.g. head.pitch)", "poll_ms", "units", "width"],
                    "use": (
                        "Display-only readout that POLLS a CORS-reachable HTTP endpoint from the "
                        "browser and shows a value from the JSON response. Generic live external "
                        "state (a device reading, a status). Not entity-bound."
                    ),
                },
                "button": {
                    "required": ["type", "label", "style (primary|secondary|danger|ghost)", "action"],
                    "optional": ["width"],
                    "action": (
                        "one of: {kind:'submit'} | {kind:'run_workflow', workflow_id, "
                        "inputs:{<name>:<expression>}, confirm?, success_message?} | "
                        "{kind:'link', href, new_tab?} | {kind:'call_connection', connection, "
                        "method?, path?, body:{<key>:<expression>}, confirm?, success_message?} "
                        "(POST straight to a saved Connection, server-side; body templated from form values)"
                    ),
                },
                "section": {
                    "required": ["type", "relationship_id", "mode (inline|modal)", "elements"],
                    "optional": ["label"],
                    "use": "A single (1:1) related record; elements are field/calculated/label.",
                },
                "table": {
                    "required": ["type", "anchor_relationship_id", "columns"],
                    "optional": ["label", "min_rows", "max_rows"],
                    "columns": (
                        "each column is {kind:'field', slug, read_only?, width?, display?} on the "
                        "1:M child, OR {kind:'related', relationship_id, slug, editable?, width?} "
                        "reached one hop across (edits create/link the related record)."
                    ),
                    "use": "Editable grid over a 1:M related entity with cross-entity columns.",
                },
                "block": {
                    "required": ["type", "anchor_relationship_id", "elements"],
                    "optional": ["label", "add_label", "min_items", "max_items"],
                    "use": "Repeating group of sub-elements mapped to a 1:M child.",
                },
                "tab_group": {
                    "required": ["type", "tabs"],
                    "tabs": "[{label, elements:[...]}]",
                },
                "panel": {
                    "required": ["type", "elements"],
                    "optional": ["title", "collapsible", "collapsed"],
                },
                "accordion": {
                    "required": ["type", "panes"],
                    "panes": "[{label, elements:[...]}]",
                },
                "columns": {
                    "required": ["type", "columns"],
                    "columns_shape": "[{span, elements:[...]}]",
                },
                "form_ref": {
                    "required": ["type", "form_id", "mode (fill|display)"],
                    "optional": ["label"],
                    "use": "VIEWS ONLY — embed an existing form.",
                },
            },
            "next": (
                "Compose your tree, then validate_form_layout(config, entity_slug) to check it, "
                "then create_form / create_view."
            ),
        }

    async def _tool_validate_form_layout(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.form import FormConfig
        from api.services.form_service import FormError, FormService

        try:
            config = FormConfig.model_validate(args.get("config") or {})
        except ValidationError as exc:
            return {"valid": False, "errors": _format_validation_errors(exc)}
        entity_slug = args.get("entity_slug")
        if not entity_slug:
            return {
                "valid": True,
                "note": (
                    "Structural check only — pass entity_slug to also verify that field slugs and "
                    "relationships exist on the entity."
                ),
            }
        definition = await EntityDefinitionRepository(session, self._org_id).get_by_slug(entity_slug)
        if definition is None:
            return {"valid": False, "errors": f"entity not found: {entity_slug!r}"}
        try:
            await FormService(session, self._org_id).validate_layout(definition.id, config)
        except FormError as exc:
            return {"valid": False, "errors": str(exc)}
        return {"valid": True}

    async def _tool_describe_workflow_actions(self, _session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        """On-demand reference for the workflow action catalog (no DB access)."""
        return {
            "notes": (
                "Workflow steps are `actions` (each {type, config}) on create_workflow / "
                "save_workflow_definition. Config values may embed {{after.<slug>}} / "
                "{{before.<slug>}} / {{inputs.<key>}} / {{vars.<key>}} tokens or a "
                '{"$ref": "after.<slug>"} envelope. `after`/`before` = triggering record (or '
                "the inbound webhook payload); `vars` = values captured from earlier steps."
            ),
            "quick_pick": {
                "POST to an external URL (simple)": "send_webhook",
                "Authenticated HTTP call via a saved connection": "http_request",
                "Answer a question from the knowledge base (RAG)": "knowledge_search",
                "Shrink text into one spoken line (small LLM)": "summarize",
                "Email someone": "send_email",
                "Email a fillable form link": "send_form",
                "Create a record in another entity": "create_record",
                "Change a field on the triggering record": "update_record_field",
                "Write an audit line": "log",
            },
            "actions": {
                "send_webhook": {
                    "config": {
                        "url": "required — target REST endpoint",
                        "body": "optional object merged into the POST",
                    },
                    "use": "POST record data (before/after) plus body to an external REST endpoint.",
                    "note": "Target host must be allow-listed (SSRF guard) or the call is blocked.",
                },
                "http_request": {
                    "config": {
                        "connection": "optional saved connection (for auth)",
                        "url or path": "target (path appends to the connection base_url)",
                        "method": "GET|POST|... (default GET)",
                        "headers": "optional object",
                        "body": "optional JSON body",
                    },
                    "use": "Authenticated HTTP call via a reusable connection; returns the parsed response.",
                    "note": "Same allow-list SSRF guard as send_webhook.",
                },
                "knowledge_search": {
                    "config": {
                        "query": "required — the question; a literal, a {{after.<slug>}}/{{vars.<key>}} token, or $ref",
                        "capture (node.data)": "name to publish the answer under, e.g. 'answer' → {{ vars.answer }}",
                    },
                    "use": "Answer a question from the org's knowledge base (RAG). Output: query/answer/sources.",
                    "note": (
                        "Read-only. To speak/store the answer, set the node's `capture` (e.g. 'kb') and "
                        "reference {{ vars.kb.answer }} in a later step — piped only on the token engine. For a "
                        "robot, condense it first with `summarize` so the spoken reply is short + citation-free."
                    ),
                },
                "summarize": {
                    "config": {
                        "text": "required — text to condense (usually {{vars.kb.answer}})",
                        "question": "optional — original question for context (e.g. {{after.text}})",
                        "max_words": "optional int (default 30)",
                        "instruction": "optional — override the spoken-style prompt",
                        "model": "optional — small model override (default OPENAI_SUMMARY_MODEL)",
                    },
                    "use": "Small-LLM condensation of text into ONE short spoken line. Output: {text, ...}.",
                    "note": (
                        "Read-only. Robot Q&A: knowledge_search (capture kb) → summarize "
                        "{text:'{{vars.kb.answer}}', question:'{{after.text}}'} (capture spoken) → "
                        "/say {text:'{{vars.spoken.text}}'}."
                    ),
                },
                "send_email": {
                    "config": {
                        "to": "recipient (or {$ref:'after.<slug>'})",
                        "subject": "",
                        "body": "supports {{after.<slug>}} tokens",
                    },
                    "use": "Send a templated email.",
                },
                "send_form": {
                    "config": {
                        "form_id": "required",
                        "recipient_field": "slug on the record",
                        "recipient": "or a literal/$ref email",
                    },
                    "use": "Mint an intake-form link bound to the triggering record and email it.",
                },
                "create_record": {
                    "config": {
                        "target_slug": "required entity slug",
                        "values": "object of field→value (tokens/$ref ok)",
                    },
                    "use": "Create a record in another entity.",
                },
                "update_record_field": {
                    "config": {"field": "required slug", "value": "value or {$ref:...}"},
                    "use": "Update one field on the triggering record.",
                },
                "log": {"config": {"message": "text"}, "use": "Emit a log line (no side effects)."},
            },
            "next": (
                "Author with create_workflow (or save_workflow_definition for a full BPMN graph), "
                "validate_workflow, then publish_workflow. Surface it from a form/view button via "
                "action {kind:'run_workflow', workflow_id, inputs}."
            ),
        }

    async def _tool_describe_bpmn_vocabulary(self, _session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        """On-demand reference for the BPMN graph shape (save_workflow_definition)."""
        return {
            "graph_shape": "{schema_version: 2, nodes: [...], edges: [...]}",
            "node": "{id (unique, [A-Za-z0-9_-], <=64), type, data:{...}, position?:{x,y}}",
            "edge": "{id, source, target, source_handle? (a gateway case key for branch edges)}",
            "node_types": {
                "trigger": "Start. data:{operations:[create|update|delete], field_filter:[slug]}. Exactly one.",
                "task": (
                    "A work step. data:{task_type, action_type, config}. task_type is the BPMN kind "
                    "(service|send|script|businessRule|user|receive|call|subProcess|manual); for our "
                    "actions use task_type 'send' or 'service' with action_type from "
                    "describe_workflow_actions (send_webhook, http_request, send_email, ...)."
                ),
                "gateway": (
                    "Branch/merge. data:{gateway_type: exclusive|parallel|inclusive|event_based, "
                    "expr?, cases?}. exclusive = if/else on `expr`; parallel = fork/join; branch "
                    "edges carry the matching case in source_handle."
                ),
                "event": (
                    "data:{position: intermediate|end|boundary, event_type: "
                    "timer|message|signal|error|escalation|terminate|none, attached_to? (boundary "
                    "host node id)}. Use a boundary timer event on a task for escalation/timeout."
                ),
            },
            "rules": (
                "One trigger; every path should reach an end event; gateway branch edges need a "
                "source_handle matching a case; boundary events set attached_to to their host task."
            ),
            "next": (
                "For a simple linear trigger→actions flow, prefer create_workflow. Otherwise build "
                "the graph, then validate_workflow (no save) to get precise issues, then "
                "save_workflow_definition, then publish_workflow."
            ),
        }

    # ---- Connections + inbound webhooks (org-admin) ----
    async def _tool_list_connections(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowConnectionRepository

        conns = await WorkflowConnectionRepository(session, self._org_id).list_all()
        return {
            "connections": [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "base_url": c.base_url,
                    "auth_type": c.auth_type,
                    "has_secret": bool(c.secret_encrypted),
                }
                for c in conns
            ]
        }

    async def _tool_create_connection(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowConnectionRepository
        from api.services.crypto import encrypt_secret

        name = args.get("name")
        if not name:
            return {"error": "name is required"}
        secret = args.get("secret")
        secret_encrypted = (
            encrypt_secret(secret, self._settings.org_encryption_key.get_secret_value()) if secret else None
        )
        conn = await WorkflowConnectionRepository(session, self._org_id).create(
            name=name,
            kind=args.get("kind", "http"),
            base_url=args.get("base_url"),
            auth_type=args.get("auth_type", "none"),
            secret_encrypted=secret_encrypted,
            config=args.get("config") or {},
        )
        return {
            "created_connection": {
                "id": str(conn.id),
                "name": conn.name,
                "base_url": conn.base_url,
                "auth_type": conn.auth_type,
            },
            "note": 'Reference it from an http_request step as {"connection": "'
            + str(conn.name)
            + '", "path": "..."}.',
        }

    async def _tool_delete_connection(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowConnectionRepository

        conn_id = _parse_uuid(args.get("connection_id"))
        if conn_id is None:
            return {"error": "connection_id is required"}
        repo = WorkflowConnectionRepository(session, self._org_id)
        conn = await repo.get(conn_id)
        if conn is None:
            return {"error": "connection not found"}
        await repo.delete(conn)
        return {"deleted_connection": str(conn_id)}

    async def _tool_list_webhooks(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowInboundEndpointRepository

        items = await WorkflowInboundEndpointRepository(session, self._org_id).list_all()
        return {
            "webhooks": [
                {
                    "id": str(e.id),
                    "name": e.name,
                    "workflow_id": str(e.workflow_id),
                    "enabled": e.enabled,
                    "signature_required": bool(e.signing_secret_encrypted),
                }
                for e in items
            ]
        }

    async def _tool_create_webhook(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        import secrets

        from api.repositories.workflow import WorkflowInboundEndpointRepository, WorkflowRepository
        from api.services.crypto import encrypt_secret
        from api.services.workflow.inbound import hash_token
        from api.services.workflow.webhook_signing import SIGNATURE_HEADER

        name = args.get("name")
        workflow_id = _parse_uuid(args.get("workflow_id"))
        if not name:
            return {"error": "name is required"}
        if workflow_id is None:
            return {"error": "workflow_id is required"}
        if await WorkflowRepository(session, self._org_id).get(workflow_id) is None:
            return {"error": "workflow not found"}
        token = secrets.token_urlsafe(32)
        signing_secret = "whsec_" + secrets.token_urlsafe(32)
        endpoint = await WorkflowInboundEndpointRepository(session, self._org_id).create(
            name=name,
            workflow_id=workflow_id,
            token_hash=hash_token(token),
            signing_secret_encrypted=encrypt_secret(
                signing_secret, self._settings.org_encryption_key.get_secret_value()
            ),
        )
        url = f"{self._settings.public_base_url.rstrip('/')}/api/inbound/{token}"
        return {
            "created_webhook": {
                "id": str(endpoint.id),
                "name": endpoint.name,
                "workflow_id": str(workflow_id),
                "url": url,
                "signing_secret": signing_secret,
                "signature_header": SIGNATURE_HEADER,
            },
            "note": (
                "URL + signing_secret are shown ONCE. The caller must sign each POST: "
                "sig = HMAC_SHA256(signing_secret, f'{t}.{raw_body}'), sent as header "
                f"'{SIGNATURE_HEADER}: t=<unix_seconds>,v1=<hex sig>'. Sign the EXACT raw body bytes "
                "you send. The bound workflow must be published to actually run."
            ),
        }

    async def _tool_delete_webhook(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.repositories.workflow import WorkflowInboundEndpointRepository

        endpoint_id = _parse_uuid(args.get("endpoint_id"))
        if endpoint_id is None:
            return {"error": "endpoint_id is required"}
        repo = WorkflowInboundEndpointRepository(session, self._org_id)
        endpoint = await repo.get(endpoint_id)
        if endpoint is None:
            return {"error": "webhook not found"}
        await repo.delete(endpoint)
        return {"deleted_webhook": str(endpoint_id)}

    # ---- Views (org-admin) ----
    async def _tool_list_views(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
        from api.services.view_service import ViewService

        views = await ViewService(session, self._org_id).list_views()
        return {"views": [{"id": str(v.id), "name": v.name, "slug": v.slug, "is_active": v.is_active} for v in views]}

    async def _tool_get_view(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.view import ViewRead
        from api.services.form_service import FormError
        from api.services.view_service import ViewService

        view_id = _parse_uuid(args.get("view_id"))
        if view_id is None:
            return {"error": "view_id is required"}
        try:
            view = await ViewService(session, self._org_id).get_view(view_id)
        except FormError as exc:
            return {"error": str(exc)}
        return {"view": ViewRead.model_validate(view).model_dump(mode="json")}

    async def _tool_create_view(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.form import FormConfig
        from api.schemas.view import ViewCreate
        from api.services.form_service import FormError
        from api.services.view_service import ViewService

        name = args.get("name")
        if not name:
            return {"error": "name is required"}
        entity_id = None
        if args.get("entity_slug"):
            definition = await EntityDefinitionRepository(session, self._org_id).get_by_slug(args["entity_slug"])
            if definition is None:
                return {"error": f"entity not found: {args['entity_slug']!r}"}
            entity_id = definition.id
        body = ViewCreate(
            name=name,
            slug=args.get("slug") or _slugify(name),
            description=args.get("description"),
            entity_definition_id=entity_id,
            config=FormConfig.model_validate(args.get("config") or {}),
        )
        try:
            view = await ViewService(session, self._org_id).create_view(body)
        except FormError as exc:
            return {"error": str(exc)}
        return {"created_view": {"id": str(view.id), "name": view.name, "slug": view.slug}}

    async def _tool_update_view(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.schemas.form import FormConfig
        from api.schemas.view import ViewUpdate
        from api.services.form_service import FormError
        from api.services.view_service import ViewService

        view_id = _parse_uuid(args.get("view_id"))
        if view_id is None:
            return {"error": "view_id is required"}
        provided: dict[str, Any] = {k: args[k] for k in ("name", "description", "is_active") if k in args}
        if "config" in args:
            provided["config"] = FormConfig.model_validate(args["config"] or {})
        try:
            view = await ViewService(session, self._org_id).update_view(view_id, ViewUpdate(**provided))
        except FormError as exc:
            return {"error": str(exc)}
        return {"updated_view": {"id": str(view.id), "name": view.name, "is_active": view.is_active}}

    async def _tool_delete_view(self, session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
        from api.services.form_service import FormError
        from api.services.view_service import ViewService

        view_id = _parse_uuid(args.get("view_id"))
        if view_id is None:
            return {"error": "view_id is required"}
        try:
            await ViewService(session, self._org_id).delete_view(view_id)
        except FormError as exc:
            return {"error": str(exc)}
        return {"deleted_view": str(view_id)}

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
            calculate_user_masks_from_membership(self._ctx.membership, org.permission_number) if org is not None else []
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

    async def _tool_list_permission_dimensions(self, session: AsyncSession, _args: dict[str, Any]) -> dict[str, Any]:
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
        result = await documents_routes.update_document(document_id=doc_id, body=body, ctx=self._ctx, session=session)
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
        return {"updated_document": {"id": str(result.id), "title": result.title, "status": result.processing_status}}

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
        result = await folders_routes.update_folder(folder_id=folder_id, body=body, ctx=self._ctx, session=session)
        return {"updated_folder": {"id": str(result.id), "name": result.name, "path": result.dot_path}}
