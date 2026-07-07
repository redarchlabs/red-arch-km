"""Workflow action framework: registry + handlers.

Each handler implements ``execute`` (real side effects) and ``simulate``
(side-effect-free, used by the dry-run test endpoint). Side-effect freedom in
test mode is structural — the test path calls a different method — not a runtime
flag sprinkled through ``execute``.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    # Allow-listed webhook hosts (SSRF guard).
    webhook_allowlist: tuple[str, ...] = ()
    # Hosts explicitly trusted to reach a private/loopback address (e.g. a
    # robot-control bridge on localhost/LAN). A host here passes the allow-list
    # check AND bypasses the private-address rejection. Matched EXACTLY.
    trusted_local_hosts: tuple[str, ...] = ()
    # Mints an intake-form link bound to (form_id, record_id) and emails the
    # recipient if given + SMTP configured. Returns (url, email_sent). None when
    # form links aren't wired (e.g. the dry-run test path).
    mint_form_link: (
        Callable[[uuid.UUID, uuid.UUID, str | None], Awaitable[tuple[str, bool]]] | None
    ) = None
    # Sends a plain email (to, subject, body). Returns True if actually sent
    # (SMTP configured), False otherwise. None on the dry-run test path.
    send_email: Callable[[str, str, str], Awaitable[bool]] | None = None
    # Resolves a named connection to a decrypted ResolvedConnection (or None if
    # absent). Built by the runner from the org's connections + encryption key;
    # None on the dry-run test path (so simulate() never touches secrets).
    resolve_connection: Callable[[str], Awaitable[ResolvedConnection | None]] | None = None


class ActionHandler(Protocol):
    type: str

    async def execute(self, ctx: ActionContext) -> dict[str, Any]: ...

    def simulate(self, ctx: ActionContext) -> dict[str, Any]: ...


ACTION_REGISTRY: dict[str, ActionHandler] = {}

# Actions that reach OUTSIDE the workspace (email/webhook/form invite). A manual
# run may only execute these when its inputs were loaded server-side from a real
# record — never against free-form, client-supplied ``before``/``after`` data.
SIDE_EFFECTING_ACTIONS: frozenset[str] = frozenset(
    {"send_email", "send_webhook", "send_form", "http_request"}
)


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
    """The triggering record, addressable as ``before.<field>`` / ``after.<field>``."""
    return {"before": ctx.before, "after": ctx.after}


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
    """Resolve every value in an action's ``values`` map (literals unchanged)."""
    return {key: _resolve_ref(value, context) for key, value in values.items()}


# ``{{ after.first_name }}`` style tokens in email subject/body/recipient.
_TEMPLATE_TOKEN = re.compile(r"\{\{\s*(before|after)\.([A-Za-z0-9_]+)\s*\}\}")


def _render_template(template: str, context: dict[str, Any]) -> str:
    """Substitute ``{{before.slug}}`` / ``{{after.slug}}`` tokens from the trigger
    context. An unknown/missing field renders as an empty string (never raises)."""

    def _sub(match: re.Match[str]) -> str:
        value = _lookup(context, f"{match.group(1)}.{match.group(2)}")
        return "" if value is None else str(value)

    return _TEMPLATE_TOKEN.sub(_sub, template)


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
        url, email_sent = await ctx.mint_form_link(
            form_uuid, ctx.record_id, str(recipient) if recipient else None
        )
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
        body = ctx.config.get("body")

        import httpx

        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            resp = await client.request(
                method, url, headers=headers, json=body if body is not None else None
            )
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


def _check_outbound_host(host: str, scheme: str, ctx: ActionContext, *, action: str) -> None:
    """Shared deny-by-default SSRF guard for outbound HTTP actions.

    A host may be reached when it is EITHER allow-listed OR listed as a trusted
    local host. Trusted local hosts additionally bypass the private/loopback-IP
    rejection — they exist precisely to reach a bridge on localhost/LAN (e.g. a
    robot-control server). Every other host must still not resolve to a private
    address, even if allow-listed (guards against a rebinding mistake). Raises
    :class:`ActionError` when the host is not permitted.
    """
    trusted = host in ctx.trusted_local_hosts
    if scheme not in ("http", "https") or (host not in ctx.webhook_allowlist and not trusted):
        raise ActionError(f"{action} host not allow-listed: {host or scheme!r}")
    if not trusted and _is_private_host(host):
        raise ActionError(f"{action} host resolves to a private address: {host}")


def _require(config: dict[str, Any], *keys: str) -> list[Any]:
    out = []
    for key in keys:
        if key not in config:
            raise ActionError(f"missing config key: {key!r}")
        out.append(config[key])
    return out
