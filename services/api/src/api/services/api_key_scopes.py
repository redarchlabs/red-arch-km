"""Canonical permission scopes for enterprise API keys.

A scope is a ``"<domain>:<action>"`` string that gates which *operations* an API
key may perform on the public ``/api/v1`` REST surface. Keys are minted
with an explicit subset of :data:`API_SCOPES`; each protected endpoint declares
the single concrete scope it requires and :func:`has_scope` decides access.

Wildcards are supported when a key is *granted* a scope (not when an endpoint
*requires* one): ``"*"`` grants everything and ``"<domain>:*"`` grants every
action in a domain. This keeps power-user keys manageable without enumerating
every action.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScopeDef:
    """A single grantable scope and its human-facing description (for the UI)."""

    name: str
    description: str


# The full catalog, grouped by domain in declaration order. Surfaced to the admin
# UI via GET /api/api-keys/scopes so the create form can render labelled checkboxes.
#
# Only scopes with a live /api/v1 endpoint are listed — advertising a grant that
# silently does nothing (e.g. entity-definition writes, document uploads, outbound
# webhooks) would give admins a false sense of least-privilege. Those ship with
# their endpoints in later phases. NOTE: a wildcard grant ("*" or "<domain>:*")
# auto-expands to future actions in that domain when they land, without re-consent.
API_SCOPES: tuple[ScopeDef, ...] = (
    ScopeDef("entities:read", "List and read custom entity definitions (schema)."),
    ScopeDef("records:read", "List and read entity records."),
    ScopeDef("records:write", "Create, update, and delete entity records."),
    ScopeDef("reports:read", "List and read saved reports."),
    ScopeDef("reports:run", "Execute reports and ad-hoc aggregations."),
    ScopeDef("workflows:read", "List workflows and inspect runs."),
    ScopeDef(
        "workflows:run",
        "Trigger workflow runs. HIGH PRIVILEGE: a key with this scope can run ANY "
        "workflow in the org, bypassing each workflow's per-user run permission.",
    ),
    ScopeDef("search:read", "Run semantic search and RAG chat over the knowledge base."),
    ScopeDef("knowledge:read", "List and read documents and folders."),
    ScopeDef("agents:read", "List agents and inspect agent runs."),
    ScopeDef(
        "agents:run",
        "Trigger an agent run. HIGH PRIVILEGE: the agent acts with all the tools + "
        "workflows its configuration grants it.",
    ),
    ScopeDef("work_orders:read", "List and read work orders."),
    ScopeDef("work_orders:write", "File and update work orders."),
)

VALID_SCOPES: frozenset[str] = frozenset(s.name for s in API_SCOPES)


def normalize_scopes(scopes: list[str]) -> list[str]:
    """Validate + de-duplicate a requested scope list, preserving catalog order.

    Accepts the concrete catalog scopes plus the ``"*"`` wildcard and per-domain
    ``"<domain>:*"`` wildcards. Raises ``ValueError`` on anything unrecognized so a
    caller can never mint a key with a typo'd (and therefore inert) scope.
    """
    requested = set(scopes)
    allowed_wildcards = {"*"} | {f"{s.name.split(':', 1)[0]}:*" for s in API_SCOPES}
    unknown = requested - VALID_SCOPES - allowed_wildcards
    if unknown:
        raise ValueError(f"Unknown scope(s): {', '.join(sorted(unknown))}")
    # Preserve a stable order: catalog scopes first, then any wildcards.
    ordered = [s.name for s in API_SCOPES if s.name in requested]
    ordered += sorted(requested - VALID_SCOPES)
    return ordered


def has_scope(granted: frozenset[str], required: str) -> bool:
    """Return whether a key holding ``granted`` scopes satisfies ``required``.

    ``required`` is always a concrete ``"domain:action"``. A grant matches when it
    is the exact scope, the global ``"*"`` wildcard, or the ``"domain:*"`` wildcard
    for that domain.
    """
    if required in granted or "*" in granted:
        return True
    domain = required.split(":", 1)[0]
    return f"{domain}:*" in granted
