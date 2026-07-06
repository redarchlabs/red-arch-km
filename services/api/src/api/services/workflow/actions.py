"""Workflow action framework: registry + handlers.

Each handler implements ``execute`` (real side effects) and ``simulate``
(side-effect-free, used by the dry-run test endpoint). Side-effect freedom in
test mode is structural — the test path calls a different method — not a runtime
flag sprinkled through ``execute``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from api.repositories.dynamic_entity import DynamicEntityRepository


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


class ActionHandler(Protocol):
    type: str

    async def execute(self, ctx: ActionContext) -> dict[str, Any]: ...

    def simulate(self, ctx: ActionContext) -> dict[str, Any]: ...


ACTION_REGISTRY: dict[str, ActionHandler] = {}


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
class SendWebhook:
    type = "send_webhook"

    async def execute(self, ctx: ActionContext) -> dict[str, Any]:
        url = ctx.config.get("url")
        if not url:
            raise ActionError("send_webhook requires url")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Deny-by-default: webhooks are disabled unless the host is explicitly
        # allow-listed (WORKFLOW_WEBHOOK_ALLOWLIST). An empty allow-list means no
        # outbound webhooks — this closes SSRF to internal services/metadata.
        if parsed.scheme not in ("http", "https") or host not in ctx.webhook_allowlist:
            raise ActionError(f"webhook host not allow-listed: {host or url!r}")
        # Defense in depth: reject a literal internal/loopback/link-local address
        # even if it were allow-listed (guards against a rebinding mistake).
        if _is_private_host(host):
            raise ActionError(f"webhook host resolves to a private address: {host}")
        import httpx

        payload = {"before": ctx.before, "after": ctx.after, **ctx.config.get("body", {})}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        return {"url": url, "status_code": resp.status_code}

    def simulate(self, ctx: ActionContext) -> dict[str, Any]:
        return {
            "would_post": ctx.config.get("url"),
            "body": {"before": ctx.before, "after": ctx.after, **ctx.config.get("body", {})},
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
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname; allow-list already gates it
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _require(config: dict[str, Any], *keys: str) -> list[Any]:
    out = []
    for key in keys:
        if key not in config:
            raise ActionError(f"missing config key: {key!r}")
        out.append(config[key])
    return out
