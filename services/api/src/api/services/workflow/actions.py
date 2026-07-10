"""Workflow action framework: registry + handlers.

Each handler implements ``execute`` (real side effects) and ``simulate``
(side-effect-free, used by the dry-run test endpoint). Side-effect freedom in
test mode is structural — the test path calls a different method — not a runtime
flag sprinkled through ``execute``.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlparse

from api.repositories.dynamic_entity import DynamicEntityRepository
from api.services.email import is_valid_email

# Outbound webhook timeout. Kept deliberately tight: the send runs INSIDE the
# dispatch DB transaction (holding a pooled connection + the claimed outbox row
# lock), so a slow endpoint directly bounds how long those resources are held.
WEBHOOK_TIMEOUT_SECONDS = 10.0


@dataclass
class ResolvedConnection:
    """A connector credential resolved (and DECRYPTED) at execute time.

    Never serialized: the plaintext ``secret`` exists only for the duration of one
    handler call and must never land in a step output, input snapshot, or log.
    """

    name: str
    base_url: str | None
    auth_type: str
    secret: str | None
    config: dict[str, Any]


@dataclass
class ActionContext:
    """Everything an action needs, bound to the current run + tenant session."""

    org_id: uuid.UUID
    record_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    config: dict[str, Any]
    # Builds a record repo for the triggering entity (writes tagged with the
    # run id so chained changes carry loop-attribution).
    trigger_repo: Callable[[], Awaitable[DynamicEntityRepository]]
    # Builds a record repo for another entity, addressed by slug.
    repo_for_slug: Callable[[str], Awaitable[DynamicEntityRepository]]
    # Caller-supplied input variables for a manual (on-demand) run, addressable as
    # ``inputs.<key>`` / ``{{ inputs.<key> }}``. Empty for record/form-triggered runs.
    inputs: dict[str, Any] = field(default_factory=dict)
    # Run-scoped variables captured from earlier steps' outputs (a task's
    # ``capture`` key). Addressable as ``vars.<key>`` / ``{{ vars.<key> }}`` so a
    # later action can consume an earlier step's result (e.g. speak a KB answer).
    vars: dict[str, Any] = field(default_factory=dict)
    # Allow-listed webhook hosts (SSRF guard).
    webhook_allowlist: tuple[str, ...] = ()
    # Hosts explicitly trusted to reach a private/loopback address (e.g. a
    # robot-control bridge on localhost/LAN). A host here passes the allow-list
    # check AND bypasses the private-address rejection. Matched EXACTLY.
    trusted_local_hosts: tuple[str, ...] = ()
    # Mints an intake-form link bound to (form_id, record_id) and emails the
    # recipient if given + SMTP configured. Returns (url, email_sent). None when
    # form links aren't wired (e.g. the dry-run test path).
    mint_form_link: Callable[[uuid.UUID, uuid.UUID, str | None], Awaitable[tuple[str, bool]]] | None = None
    # Sends a plain email (to, subject, body). Returns True if actually sent
    # (SMTP configured), False otherwise. None on the dry-run test path.
    send_email: Callable[[str, str, str], Awaitable[bool]] | None = None
    # Resolves a named connection to a decrypted ResolvedConnection (or None if
    # absent). Built by the runner from the org's connections + encryption key;
    # None on the dry-run test path (so simulate() never touches secrets).
    resolve_connection: Callable[[str], Awaitable[ResolvedConnection | None]] | None = None
    # Runs an org-scoped RAG query against the knowledge base and returns
    # ``{"answer": str, "sources": [...]}``. Built by the runner from Settings +
    # the brain-api client; None on the dry-run test path (so simulate() makes no
    # network call).
    # Hybrid RAG lookup: given ``{"query": str, "use_knowledge_graph": bool}`` returns
    # ``{"answer", "sources"}``. ``use_knowledge_graph`` lets a per-run toggle skip the
    # (sequential) graph hop for speed. None on the dry-run test path.
    search_knowledge: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
    # Retrieval-ONLY KB lookup (no brain-api LLM synthesis): returns the matched
    # passages pre-formatted as context — ``{"answer": <passage text>, "sources":
    # [...], "passages": [...]}``. Used by ``knowledge_search`` when
    # ``synthesize: false`` so a downstream ``llm_decide`` grounds on the raw
    # passages and does the ONE generation itself (one LLM call per turn instead of
    # brain-api RAG + llm_decide). None on the dry-run test path.
    retrieve_knowledge: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    # Compresses text into one short spoken line via a small LLM (opts: text,
    # question, max_words, instruction, model) → the condensed string. Built by the
    # runner from Settings + the org's OpenAI key; None on the dry-run test path.
    summarize: Callable[[dict[str, Any]], Awaitable[str]] | None = None
    # Constrained-LLM steering for a robot: given (system, question, context,
    # gestures, moods, history, model) returns a structured decision dict
    # ``{say, gesture, mood, done, reason}`` with gesture/mood locked to the passed
    # vocabulary. Built by the runner; None on the dry-run test path.
    decide: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None


class ActionHandler(Protocol):
    type: str

    async def execute(self, ctx: ActionContext) -> dict[str, Any]: ...

    def simulate(self, ctx: ActionContext) -> dict[str, Any]: ...


ACTION_REGISTRY: dict[str, ActionHandler] = {}

# Actions that reach OUTSIDE the workspace (email/webhook/form invite). A manual
# run may only execute these when its inputs were loaded server-side from a real
# record — never against free-form, client-supplied ``before``/``after`` data.
SIDE_EFFECTING_ACTIONS: frozenset[str] = frozenset({"send_email", "send_webhook", "send_form", "http_request"})


def register(cls: type[ActionHandler]) -> type[ActionHandler]:
    """Register an *instance* of the handler class under its ``type``."""
    ACTION_REGISTRY[cls.type] = cls()
    return cls


class ActionError(Exception):
    """Raised when an action config is invalid or execution fails."""


# A config value may be a literal, or a *reference* to a field on the triggering
# record: the envelope ``{"$ref": "after.<field>"}`` (``before.``/``after.``).
# The ``$`` prefix makes it unambiguous against a literal JSON-field object.
_REF_KEY = "$ref"


def _trigger_context(ctx: ActionContext) -> dict[str, Any]:
    """The run's data context: the triggering record (``before.<field>`` /
    ``after.<field>``), any manual-run input variables (``inputs.<key>``), and
    variables captured from earlier steps (``vars.<key>``)."""
    return {"before": ctx.before, "after": ctx.after, "inputs": ctx.inputs or {}, "vars": ctx.vars or {}}


def _lookup(context: dict[str, Any], path: str) -> Any:
    """Resolve a dotted ``after.first_name`` path against the trigger context;
    a missing segment yields ``None`` (so the target field is simply unset)."""
    cur: Any = context
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _resolve_ref(value: Any, context: dict[str, Any]) -> Any:
    """Resolve a ``{"$ref": "after.x"}`` envelope; pass any other value through."""
    if isinstance(value, dict) and list(value) == [_REF_KEY] and isinstance(value[_REF_KEY], str):
        return _lookup(context, value[_REF_KEY])
    return value


def _resolve_values(values: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Resolve every value in an action's ``values`` map (literals unchanged).

    Only unwraps ``{"$ref": ...}`` envelopes — does NOT render ``{{ }}`` templates.
    Use :func:`_resolve_value_map` where the config documents ``{{ }}`` support."""
    return {key: _resolve_ref(value, context) for key, value in values.items()}


def _resolve_value_map(values: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Resolve every value in a map, rendering BOTH ``{"$ref": ...}`` envelopes and
    ``{{ }}`` template strings (via :func:`_resolve_dynamic`). Used for action config
    that documents template support (e.g. ``update_record`` values, record filters),
    so ``{"note": "Hi {{after.name}}"}`` writes the rendered value, not the literal."""
    return {key: _resolve_dynamic(value, context) for key, value in values.items()}


# ``{{ after.first_name }}`` / ``{{ inputs.amount }}`` / ``{{ vars.answer }}``
# tokens in email subject/body/recipient (and any other templated action field).
# The path after the namespace may be dotted (``{{ vars.kb.answer }}``) so a token
# can reach into a captured step output (e.g. knowledge_search's {answer, sources}).
_TEMPLATE_TOKEN = re.compile(r"\{\{\s*(before|after|inputs|vars)\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}")


def _render_template(template: str, context: dict[str, Any]) -> str:
    """Substitute ``{{before.slug}}`` / ``{{after.slug}}`` tokens from the trigger
    context. An unknown/missing field renders as an empty string (never raises)."""

    def _sub(match: re.Match[str]) -> str:
        value = _lookup(context, f"{match.group(1)}.{match.group(2)}")
        return "" if value is None else str(value)

    return _TEMPLATE_TOKEN.sub(_sub, template)


def _render_deep(value: Any, context: dict[str, Any]) -> Any:
    """Render ``{{...}}`` tokens in every string leaf of a nested body (dict/list),
    leaving non-string scalars untouched. Used for an action's JSON body so e.g. a
    robot ``/say`` can carry ``{"text": "{{vars.kb.answer}}"}``."""
    if isinstance(value, str):
        return _render_template(value, context)
    if isinstance(value, dict):
        return {key: _render_deep(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_deep(item, context) for item in value]
    return value


def _resolve_dynamic(raw: Any, context: dict[str, Any]) -> Any:
    """Resolve a config value that may be a ``{"$ref": "inputs.x"}`` envelope (typed
    value preserved), a ``{{ inputs.x }}`` template string (rendered to text), or a
    plain literal (passed through). Lets a per-run toggle steer a node's behaviour."""
    if isinstance(raw, dict):
        return _resolve_ref(raw, context)
    if isinstance(raw, str):
        return _render_template(raw, context)
    return raw


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce a resolved config value to a bool. ``None``/empty → ``default`` so an
    unset (or empty-rendered) toggle keeps the back-compat behaviour; strings like
    ``"true"``/``"1"``/``"on"`` are truthy so a ``{{ inputs.flag }}`` template works."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _as_int(value: Any, default: int) -> int:
    """Coerce a resolved config value to an int, tolerating a ``{{ inputs.n }}``
    template that rendered to a numeric string. ``None``/blank/garbage → ``default``."""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _jsonable(value: Any) -> Any:
    """Recursively convert a record's Python-typed field values into JSON-safe
    scalars so a read (``get_record``) can be stored as a step output / run
    variable. Records carry ``uuid.UUID`` ids, ``datetime``/``date`` timestamps,
    and ``Decimal`` numerics that the run-step JSON writer can't serialize as-is."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _resolve_record_id(ctx: ActionContext, context: dict[str, Any], *, action: str) -> uuid.UUID:
    """Resolve + parse a ``record_id`` config value (literal / ``$ref`` / ``{{ }}``)
    into a UUID, raising a clean ActionError on a missing or malformed id."""
    raw = _resolve_dynamic(ctx.config.get("record_id"), context)
    if raw is None or raw == "":
        raise ActionError(f"{action} mode 'by_id' requires record_id")
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise ActionError(f"{action}: invalid record_id {raw!r}") from exc


async def _resolve_singleton_id(
    repo: DynamicEntityRepository, ctx: ActionContext, context: dict[str, Any], mode: str
) -> dict[str, Any] | None:
    """Return the record for a ``latest`` / ``first`` lookup (newest / oldest by
    ``created_at``), honouring an optional resolved ``filters`` map. ``None`` when
    the entity has no matching record."""
    filters = _resolve_value_map(ctx.config.get("filters", {}) or {}, context)
    items, _ = await repo.list(
        filters=filters or None,
        limit=1,
        order_by="created_at",
        order_dir="desc" if mode == "latest" else "asc",
    )
    return items[0] if items else None


@register
class UpdateRecordField:
    type = "update_record_field"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        field, value = _require(ctx.config, "field", "value")
        if ctx.record_id is None:
            raise ActionError("update_record_field requires a triggering record")
        value = _resolve_ref(value, _trigger_context(ctx))
        repo = await ctx.trigger_repo()
        updated = await repo.update(ctx.record_id, {field: value})
        return {"record_id": str(ctx.record_id), "field": field, "value": value, "updated": updated is not None}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        field = ctx.config.get("field")
        return {
            "record_id": str(ctx.record_id) if ctx.record_id else None,
            "field": field,
            "old": (ctx.after or {}).get(field),
            "new": _resolve_ref(ctx.config.get("value"), _trigger_context(ctx)),
        }


@register
class CreateRecord:
    type = "create_record"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        target_slug = ctx.config.get("target_slug")
        if not target_slug:
            raise ActionError("create_record requires target_slug")
        values = _resolve_values(ctx.config.get("values", {}), _trigger_context(ctx))
        repo = await ctx.repo_for_slug(target_slug)
        created = await repo.create(values)
        return {"target_slug": target_slug, "created_id": str(created["id"])}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        return {
            "target_slug": ctx.config.get("target_slug"),
            "values": _resolve_values(ctx.config.get("values", {}), _trigger_context(ctx)),
        }


@register
class GetRecord:
    """Read a record's current field values into a run variable.

    The read-back the engine otherwise lacks: ``update_record_field`` only touches
    the triggering record and there is no other way to load a record's live state.
    Capture the output and read fields as ``{{ vars.state.<slug> }}`` — e.g. feed a
    live "mission status" into a downstream ``summarize`` / ``llm_decide`` / ``/say``.

    Config: ``{"target_slug": "mission_state", "mode": "latest", "capture": "state"}``.
    ``mode``: ``"latest"`` (newest by created_at, the default when no ``record_id``),
    ``"first"`` (oldest), or ``"by_id"`` with ``record_id`` (literal / ``$ref`` /
    ``{{ }}``). ``filters`` optionally narrows ``latest``/``first``. Output is the
    record's slug-keyed fields (plus ``id``/``created_at``/``updated_at``), or ``{}``
    when no record matches — so a gateway can branch on ``{{ vars.state.id }}``.
    Read-only."""

    type = "get_record"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        target_slug = ctx.config.get("target_slug")
        if not target_slug:
            raise ActionError("get_record requires target_slug")
        context = _trigger_context(ctx)
        # Empty-string record_id (e.g. an unresolved template) is treated as absent
        # so mode falls back to latest rather than hard-erroring in by_id.
        mode = str(ctx.config.get("mode") or ("by_id" if ctx.config.get("record_id") else "latest")).lower()
        repo = await ctx.repo_for_slug(str(target_slug))
        if mode == "by_id":
            record = await repo.get(_resolve_record_id(ctx, context, action="get_record"))
        elif mode in ("latest", "first"):
            record = await _resolve_singleton_id(repo, ctx, context, mode)
        else:
            raise ActionError(f"get_record: unknown mode {mode!r}")
        return _jsonable(record) if record else {}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        # Read-only, but keep the dry run out of the DB for symmetry with the
        # other network/DB actions.
        return {"target_slug": ctx.config.get("target_slug"), "mode": ctx.config.get("mode", "latest"), "id": None}


@register
class UpdateRecord:
    """Write multiple fields of a targeted record in one node.

    Richer sibling of ``update_record_field`` (which writes ONE field of the
    triggering record only). Targets an arbitrary entity + record so a manual or
    entity-triggered workflow can maintain shared state (e.g. a mission-state row).

    Config: ``{"target_slug": "mission_state", "mode": "latest",
    "values": {"alert_level": {"$ref": "inputs.alert_level"}, "phase": "Crisis"}}``.
    Target resolution mirrors ``get_record``: ``mode`` ``"latest"``/``"first"``/
    ``"by_id"`` (with ``record_id``) + optional ``filters``. Omit ``target_slug`` to
    update the TRIGGERING record (back-compat with ``update_record_field``). Each
    value may be a literal, a ``$ref`` envelope, or a ``{{ }}`` template.

    Writes emit a record-change event (fires entity-triggered workflows) — an
    announcer keyed off this entity must only READ + act, never write it back, or
    it will loop."""

    type = "update_record"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        values_cfg = ctx.config.get("values")
        if not isinstance(values_cfg, dict) or not values_cfg:
            raise ActionError("update_record requires a non-empty values map")
        context = _trigger_context(ctx)
        values = _resolve_value_map(values_cfg, context)
        target_slug = ctx.config.get("target_slug")
        if target_slug:
            repo = await ctx.repo_for_slug(str(target_slug))
            record_id = await self._target_id(ctx, repo, context)
        else:
            if ctx.record_id is None:
                raise ActionError("update_record requires a triggering record or a target_slug")
            repo = await ctx.trigger_repo()
            record_id = ctx.record_id
        updated = await repo.update(record_id, values)
        return {
            "target_slug": target_slug,
            "record_id": str(record_id),
            "updated": updated is not None,
            "values": _jsonable(values),
        }

    async def _target_id(self, ctx: ActionContext, repo: DynamicEntityRepository, context: dict[str, Any]) -> uuid.UUID:
        mode = str(ctx.config.get("mode") or ("by_id" if ctx.config.get("record_id") else "latest")).lower()
        if mode == "by_id":
            return _resolve_record_id(ctx, context, action="update_record")
        if mode in ("latest", "first"):
            record = await _resolve_singleton_id(repo, ctx, context, mode)
            if record is None:
                slug = ctx.config.get("target_slug")
                raise ActionError(f"update_record: no record found for mode {mode!r} on {slug!r}")
            return uuid.UUID(str(record["id"]))
        raise ActionError(f"update_record: unknown mode {mode!r}")

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        return {
            "target_slug": ctx.config.get("target_slug"),
            "values": _jsonable(_resolve_value_map(ctx.config.get("values", {}) or {}, _trigger_context(ctx))),
        }


@register
class SendForm:
    """Mint an intake-form link for the triggering record and email it.

    Config: ``{"form_id": "<uuid>", "recipient": {"$ref": "after.email"}}`` (or a
    literal email string). The link is bound to the triggering record so the
    recipient's submission updates it."""

    type = "send_form"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        form_id = ctx.config.get("form_id")
        if not form_id:
            raise ActionError("send_form requires form_id")
        if ctx.record_id is None:
            raise ActionError("send_form requires a triggering record")
        if ctx.mint_form_link is None:
            raise ActionError("form links are not available in this context")
        # Recipient: prefer a field on the triggering record (recipient_field →
        # after.<slug>), then a $ref, then a literal.
        recipient = None
        recipient_field = ctx.config.get("recipient_field")
        if recipient_field:
            recipient = (ctx.after or {}).get(recipient_field)
        if not recipient:
            recipient = _resolve_ref(ctx.config.get("recipient"), _trigger_context(ctx))
        try:
            form_uuid = uuid.UUID(str(form_id))
        except ValueError as exc:
            raise ActionError(f"invalid form_id: {form_id!r}") from exc
        url, email_sent = await ctx.mint_form_link(form_uuid, ctx.record_id, str(recipient) if recipient else None)
        return {"form_id": str(form_id), "url": url, "email_sent": email_sent}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        return {
            "form_id": ctx.config.get("form_id"),
            "recipient": _resolve_ref(ctx.config.get("recipient"), _trigger_context(ctx)),
        }


@register
class SendEmail:
    """Send a templated email.

    Config: ``{"to": "...", "subject": "...", "body": "..."}``. Each field may
    embed ``{{after.<slug>}}`` / ``{{before.<slug>}}`` tokens resolved against
    the triggering record; ``to`` may instead be a ``{"$ref": "after.email"}``
    envelope. Delivery is a no-op (``sent=False``) when SMTP isn't configured."""

    type = "send_email"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        if ctx.send_email is None:
            raise ActionError("email is not available in this context")
        template_ctx = _trigger_context(ctx)
        raw_to = ctx.config.get("to")
        if isinstance(raw_to, dict):
            # A $ref recipient envelope (e.g. {"$ref": "after.email"}).
            resolved = _resolve_ref(raw_to, template_ctx)
            to = str(resolved).strip() if resolved else ""
        else:
            to = _render_template(str(raw_to or ""), template_ctx).strip()
        if not to:
            raise ActionError("send_email requires a recipient (to)")
        # Validate the resolved recipient before handing it to smtplib: a
        # templated value could be malformed or carry a CR/LF header-injection
        # payload. A bad address fails this action, not the SMTP conversation.
        if not is_valid_email(to):
            raise ActionError(f"send_email recipient is not a valid address: {to!r}")
        subject = _render_template(str(ctx.config.get("subject", "")), template_ctx)
        body = _render_template(str(ctx.config.get("body", "")), template_ctx)
        sent = await ctx.send_email(to, subject, body)
        return {"to": to, "subject": subject, "sent": sent}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        template_ctx = _trigger_context(ctx)
        return {
            "to": _render_template(str(ctx.config.get("to", "")), template_ctx),
            "subject": _render_template(str(ctx.config.get("subject", "")), template_ctx),
            "body": _render_template(str(ctx.config.get("body", "")), template_ctx),
        }


@register
class SendWebhook:
    type = "send_webhook"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        url = ctx.config.get("url")
        if not url:
            raise ActionError("send_webhook requires url")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Deny-by-default SSRF guard: host must be allow-listed (or a trusted
        # local host) and must not resolve to a private address unless trusted.
        _check_outbound_host(host, parsed.scheme, ctx, action="webhook")
        # Deferred import: httpx is only needed when a webhook actually fires,
        # keeping it off the hot import path for the common no-webhook workflow.
        import httpx

        payload = {"before": ctx.before, "after": ctx.after, **ctx.config.get("body", {})}
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
        return {"url": url, "status_code": resp.status_code}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        return {
            "would_post": ctx.config.get("url"),
            "body": {"before": ctx.before, "after": ctx.after, **ctx.config.get("body", {})},
        }


@register
class HttpRequest:
    """Authenticated HTTP call via a reusable connection (the connector task).

    Resolves ``config.connection`` to a ResolvedConnection, injects its auth
    (bearer / api-key header / basic), and calls ``base_url + config.path`` (or a
    literal ``config.url``). The same deny-by-default SSRF guard as send_webhook
    applies — the target host must be allow-listed and must not be a private IP.
    The parsed response is returned as the step output (and can be captured into a
    run variable via the task's ``capture``); the secret is NEVER echoed back.
    """

    type = "http_request"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        conn: ResolvedConnection | None = None
        name = ctx.config.get("connection")
        if name:
            if ctx.resolve_connection is None:
                raise ActionError("connections are not available in this context")
            conn = await ctx.resolve_connection(str(name))
            if conn is None:
                raise ActionError(f"connection not found: {name!r}")

        base = (conn.base_url if conn and conn.base_url else "") or ""
        url = ctx.config.get("url") or (base.rstrip("/") + "/" + str(ctx.config.get("path", "")).lstrip("/"))
        parsed = urlparse(url)
        host = parsed.hostname or ""
        _check_outbound_host(host, parsed.scheme, ctx, action="http_request")

        method = str(ctx.config.get("method", "GET")).upper()
        headers: dict[str, str] = {}
        for key, value in (ctx.config.get("headers") or {}).items():
            headers[str(key)] = str(value)
        headers.update(_auth_headers(conn))
        # Render ``{{after.x}}`` / ``{{vars.kb.answer}}`` tokens in the JSON body so a
        # request can carry values from the trigger or an earlier captured step
        # (e.g. speak a knowledge_search answer). The URL/host is intentionally NOT
        # templated — it stays under the connection + SSRF allow-list.
        body = _render_deep(ctx.config.get("body"), _trigger_context(ctx))

        import httpx

        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            resp = await client.request(method, url, headers=headers, json=body if body is not None else None)
        try:
            parsed_body: Any = resp.json()
        except Exception:  # noqa: BLE001 - any non-JSON body falls back to text
            parsed_body = resp.text
        return {"status_code": resp.status_code, "ok": resp.is_success, "body": parsed_body}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        # Never resolve/echo the secret in a dry run.
        return {
            "would_request": {
                "method": str(ctx.config.get("method", "GET")).upper(),
                "connection": ctx.config.get("connection"),
                "url": ctx.config.get("url") or ctx.config.get("path"),
            }
        }


def _auth_headers(conn: ResolvedConnection | None) -> dict[str, str]:
    """Build auth headers from a resolved connection. Secret used here only."""
    if conn is None or conn.auth_type == "none" or not conn.secret:
        return {}
    if conn.auth_type == "bearer":
        return {"Authorization": f"Bearer {conn.secret}"}
    if conn.auth_type == "api_key":
        header_name = str(conn.config.get("header", "X-API-Key"))
        return {header_name: conn.secret}
    if conn.auth_type == "basic":
        import base64

        username = str(conn.config.get("username", ""))
        token = base64.b64encode(f"{username}:{conn.secret}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


@register
class KnowledgeSearch:
    """Answer a question from the org's knowledge base (hybrid RAG).

    Config: ``{"query": "<text>", "capture": "answer", "synthesize": true}`` where
    ``query`` may be a literal, a ``{{ after.<slug> }}`` / ``{{ vars.<key> }}``
    template, or a ``{"$ref": "after.<slug>"}`` envelope — so a workflow triggered
    by a robot's "heard" webhook can look up ``{{ after.text }}``. The step output
    is ``{"query", "answer", "sources"}``; wire the node's ``capture`` to publish
    ``answer`` as a run variable a later ``/say`` step can speak via
    ``{{ vars.answer }}``. Read-only (queries the KB, mutates nothing).

    ``synthesize`` (default ``true``) controls whether brain-api runs its own LLM
    to compose the answer. Set ``synthesize: false`` for RETRIEVAL-ONLY: ``answer``
    is then the raw matched passages (no brain-api LLM), meant to feed a downstream
    ``llm_decide`` that does the single grounded generation itself — one LLM call
    per turn instead of two (brain-api RAG synthesis + llm_decide)."""

    type = "knowledge_search"

    def _query(self, ctx: ActionContext) -> str:
        raw = ctx.config.get("query")
        context = _trigger_context(ctx)
        resolved = _resolve_ref(raw, context) if isinstance(raw, dict) else _render_template(str(raw or ""), context)
        return str(resolved or "").strip()

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        query = self._query(ctx)
        if not query:
            raise ActionError("knowledge_search requires a non-empty query")
        context = _trigger_context(ctx)
        # ``synthesize`` and ``use_knowledge_graph`` may be literals OR per-run
        # values (``{{ inputs.synthesize }}`` / ``{"$ref": "inputs.use_knowledge_graph"}``)
        # so a caller such as the robot chat's Fast-mode / Knowledge-graph toggles can
        # steer each turn. Both default true (back-compat): brain-api synthesizes with
        # graph context. ``synthesize:false`` = retrieval-only, leaving generation to a
        # downstream step (llm_decide or summarize) — one LLM call instead of two.
        synthesize = _as_bool(_resolve_dynamic(ctx.config.get("synthesize"), context), True)
        use_knowledge_graph = _as_bool(_resolve_dynamic(ctx.config.get("use_knowledge_graph"), context), True)
        if not synthesize:
            if ctx.retrieve_knowledge is None:
                raise ActionError("knowledge retrieval is not available in this context")
            result = await ctx.retrieve_knowledge(query)
            return {
                "query": query,
                "answer": result.get("answer", ""),
                "sources": result.get("sources", []),
                "passages": result.get("passages", []),
            }
        if ctx.search_knowledge is None:
            raise ActionError("knowledge search is not available in this context")
        result = await ctx.search_knowledge({"query": query, "use_knowledge_graph": use_knowledge_graph})
        return {
            "query": query,
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
        }

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        # Never hit the network in a dry run.
        return {"query": self._query(ctx), "answer": "<knowledge search result>", "sources": []}


@register
class Summarize:
    """Compress text into one short, natural spoken line via a small LLM.

    Config: ``{"text": "{{vars.kb.answer}}", "question": "{{after.text}}",
    "max_words": 25, "instruction"?: "...", "model"?: "gpt-5-nano"}``. ``text`` (and
    ``question``) may be literals, templates, or ``$ref`` envelopes. Intended to sit
    between ``knowledge_search`` and a robot ``/say`` so the robot speaks a precise
    one-liner instead of a full RAG answer with citations. Output: ``{text, ...}``."""

    type = "summarize"

    def _field(self, ctx: ActionContext, key: str) -> str:
        raw = ctx.config.get(key)
        context = _trigger_context(ctx)
        resolved = _resolve_ref(raw, context) if isinstance(raw, dict) else _render_template(str(raw or ""), context)
        return str(resolved or "").strip()

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        text = self._field(ctx, "text")
        if not text:
            raise ActionError("summarize requires non-empty text")
        if ctx.summarize is None:
            raise ActionError("summarization is not available in this context")
        question = self._field(ctx, "question")
        context = _trigger_context(ctx)
        # ``max_words`` and ``model`` may be literals or per-run values
        # (``{{ inputs.max_words }}`` / ``{"$ref": "inputs.answer_model"}``) so the robot
        # chat's Concise / Answer-model toggles steer the spoken reply. A blank ``model``
        # resolves to None → the runner falls back to the org's default summary model.
        max_words = _as_int(_resolve_dynamic(ctx.config.get("max_words"), context), 30)
        model = str(_resolve_dynamic(ctx.config.get("model"), context) or "").strip() or None
        spoken = await ctx.summarize(
            {
                "text": text,
                "question": question or None,
                "max_words": max_words,
                "instruction": ctx.config.get("instruction"),
                "model": model,
            }
        )
        return {"text": spoken, "input_chars": len(text), "output_chars": len(spoken)}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        # Never call the LLM in a dry run.
        text = self._field(ctx, "text")
        return {"text": "<summarized spoken reply>", "input_chars": len(text), "output_chars": 0}


@register
class LlmDecide:
    """Constrained-LLM steering step for a workflow-driven robot.

    The workflow is the controller; this node uses an LLM only to *pick the next move*
    within the robot's advertised vocabulary. It returns a STRUCTURED decision
    ``{say, gesture, mood, done, reason}`` — ``gesture``/``mood`` are enum-locked to the
    ``gestures``/``moods`` config (source these from the robot's ``GET /capabilities``), and
    ``done`` lets an exclusive gateway loop or finish (goal-directed). Wire the node's
    ``capture`` (e.g. ``"decision"``) so downstream ``http_request`` steps template
    ``{{vars.decision.say}}`` / ``{{vars.decision.gesture}}``.

    Config: ``{"question": "{{after.text}}", "context": "{{vars.kb.answer}}",
    "system": "<rules of engagement>", "gestures": [...], "moods": [...],
    "history"?: [...], "model"?: "..."}``. Read-only (calls the LLM, mutates nothing)."""

    type = "llm_decide"

    def _field(self, ctx: ActionContext, key: str) -> str:
        raw = ctx.config.get(key)
        context = _trigger_context(ctx)
        resolved = _resolve_ref(raw, context) if isinstance(raw, dict) else _render_template(str(raw or ""), context)
        return str(resolved or "").strip()

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        if ctx.decide is None:
            raise ActionError("llm_decide is not available in this context")
        question = self._field(ctx, "question")
        if not question:
            raise ActionError("llm_decide requires a non-empty question")
        gestures = [str(g) for g in (ctx.config.get("gestures") or [])]
        moods = [str(m) for m in (ctx.config.get("moods") or [])]
        decision = await ctx.decide(
            {
                "system": self._field(ctx, "system") or None,
                "question": question,
                "context": self._field(ctx, "context"),
                "gestures": gestures,
                "moods": moods,
                "history": ctx.config.get("history"),
                "model": ctx.config.get("model"),
            }
        )
        # Defense-in-depth: enforce the vocabulary even if a model ignored the schema, so a
        # downstream /gesture or /mood call can never be handed a move the robot rejects.
        if decision.get("gesture") not in gestures:
            decision["gesture"] = None
        if decision.get("mood") not in moods:
            decision["mood"] = None
        decision["done"] = bool(decision.get("done"))
        return decision

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        # Never call the LLM in a dry run.
        return {
            "say": "<decided spoken reply>",
            "gesture": None,
            "mood": None,
            "done": True,
            "reason": "dry-run",
        }


@register
class LogAction:
    """A no-side-effect action, useful for testing and audit breadcrumbs."""

    type = "log"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        return {"logged": ctx.config.get("message", "")}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        return {"logged": ctx.config.get("message", "")}


def _is_private_host(host: str) -> bool:
    """True if ``host`` is a literal private/loopback/link-local IP address."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname; allow-list already gates it
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def assert_outbound_host_allowed(
    host: str,
    scheme: str,
    *,
    webhook_allowlist: tuple[str, ...],
    trusted_local_hosts: tuple[str, ...],
    action: str,
) -> None:
    """Shared deny-by-default SSRF guard for outbound HTTP (the raw form).

    A host may be reached when it is EITHER allow-listed OR listed as a trusted
    local host. Trusted local hosts additionally bypass the private/loopback-IP
    rejection — they exist precisely to reach a bridge on localhost/LAN (e.g. a
    robot-control server). Every other host must still not resolve to a private
    address, even if allow-listed (guards against a rebinding mistake). Raises
    :class:`ActionError` when the host is not permitted. Reused by the form
    ``call_connection`` endpoint so button-driven calls get the identical guard.
    """
    trusted = host in trusted_local_hosts
    if scheme not in ("http", "https") or (host not in webhook_allowlist and not trusted):
        raise ActionError(f"{action} host not allow-listed: {host or scheme!r}")
    if not trusted and _is_private_host(host):
        raise ActionError(f"{action} host resolves to a private address: {host}")


def _check_outbound_host(host: str, scheme: str, ctx: ActionContext, *, action: str) -> None:
    """SSRF guard bound to an :class:`ActionContext` (workflow-execution path)."""
    assert_outbound_host_allowed(
        host,
        scheme,
        webhook_allowlist=ctx.webhook_allowlist,
        trusted_local_hosts=ctx.trusted_local_hosts,
        action=action,
    )


def _require(config: dict[str, Any], *keys: str) -> list[Any]:
    out = []
    for key in keys:
        if key not in config:
            raise ActionError(f"missing config key: {key!r}")
        out.append(config[key])
    return out
