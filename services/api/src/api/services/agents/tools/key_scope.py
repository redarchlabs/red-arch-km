"""What a run started through an API key may do.

Every run started through ``/api/v1`` (and every run spawned from one) carries
``via_api_key`` and the key's id (``agent_runs.api_key_id``). It has no actor: a key
is not a person, so the agent never reads with anyone's personal reach.

* A run started by an **org key** keeps today's behaviour: its knowledge reads need
  the agent's explicit ``knowledge_scope: "org"`` grant, like any unattended run —
  even when a person started the key-filed work order it belongs to.
* A run started by a **scoped key** (dimension and/or folder assignments) reads with
  the key's own masks and folder set, re-resolved by the tools that use them
  (:func:`run_key_scope`), so changing a folder applies to a run already going.
  Most agent tools are not mask-aware — records, workflows, work-order artifacts,
  other runs' details, batch generation, local execution and MCP servers would all
  reach org-wide data — so such a run gets an allowlist:

  * ``search_knowledge`` — the key's masks and folders, own org only, never
    unrestricted.
  * ``create_document`` — needs ``knowledge:write`` on the (still valid) key (org
    keys too), counts against the key's daily write cap, and a folder inside the
    key's folder set that its masks may add to. ``attach_document`` is not on the
    list (it sits beside work-order artifact tools that read org-wide), but it
    applies the same write gate itself, so an org key's run needs
    ``knowledge:write`` and the daily cap for it too.
  * work-order task tools, ``submit_plan``, and the workflow-bridge
    ``complete_task`` / ``escalate_task`` — the run's own order.
  * ``delegate_task``, ``escalate``, ``consult_peer``, ``reply_to_peer``,
    ``ask_human``, ``request_review`` — spawned runs inherit the same limits.
  * ``web_research``, ``fetch_web_page`` — the public web, no org data.

  Everything else is withheld from the tool list and refused again at dispatch.

**Every tool call of a key-started run re-checks the key** (:func:`run_key_status`:
one primary-key read — active, unexpired, same org, a known access mode — never the
full mask/folder resolution). A key that is gone (deleted → ``api_key_id`` is NULL,
revoked, expired, in another org, invalid) refuses EVERY tool, allowlisted ones
included (:data:`KEY_GONE`), and the dispatcher ends the run with status
``error``. A scoped key whose scope no longer resolves is refused by the tools that
resolve it — never treated as org-wide.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from api.models.api_key import ACCESS_MODE_ORG, ACCESS_MODE_SCOPED, ApiKey
from api.services.api_key_scope import KeyScopeError, resolve_key_scope
from api.services.api_key_service import is_expired
from api.services.permission_config import TooManyAccessMasks

logger = logging.getLogger(__name__)

SCOPED_KEY_RUN_TOOLS: frozenset[str] = frozenset(
    {
        "search_knowledge",
        "create_document",
        "set_work_order_tasks",
        "update_work_order_task",
        "list_work_order_tasks",
        "submit_plan",
        "complete_task",
        "escalate_task",
        "delegate_task",
        "escalate",
        "consult_peer",
        "reply_to_peer",
        "ask_human",
        "request_review",
        "web_research",
        "fetch_web_page",
    }
)

KEY_GONE = "The API key that started this run is no longer valid (revoked, expired or deleted)."


class RunKeyRefused(Exception):
    """The run's key no longer grants anything; the message is safe for the model."""


class KeyState(StrEnum):
    GONE = "gone"  # deleted, revoked, expired, another org's, or an unknown mode
    ORG = "org"
    SCOPED = "scoped"


@dataclass(frozen=True, slots=True)
class RunKeyStatus:
    """The run's key as of this tool call, without resolving its scope."""

    state: KeyState
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RunKeyScope:
    """The run's key as of this tool call."""

    scoped: bool
    scopes: frozenset[str]
    # Scoped keys only (None for an org key): never empty masks; folder_ids None =
    # no folder limit, else the expanded, visibility-capped set (may be empty).
    masks: tuple[int, ...] | None = None
    folder_ids: frozenset[uuid.UUID] | None = None


async def run_key_status(ctx: Any) -> RunKeyStatus | None:
    """Reload the run's API key now — one primary-key read, no scope resolution.

    ``None`` when the run was not started by a key. Cheap enough for every tool call.
    """
    if not getattr(ctx, "via_api_key", False):
        return None
    key_id = getattr(ctx, "api_key_id", None)
    session = getattr(ctx, "session", None)
    if key_id is None or session is None:
        return RunKeyStatus(KeyState.GONE)
    key = await session.get(ApiKey, key_id, populate_existing=True)
    if key is None or key.org_id != ctx.org_id or key.revoked_at is not None or is_expired(key, now=datetime.now(UTC)):
        return RunKeyStatus(KeyState.GONE)
    if key.access_mode == ACCESS_MODE_SCOPED:
        state = KeyState.SCOPED
    elif key.access_mode == ACCESS_MODE_ORG:
        state = KeyState.ORG
    else:
        logger.warning(
            "run %s: API key %s has unknown access mode %r", getattr(ctx, "run_id", None), key_id, key.access_mode
        )
        return RunKeyStatus(KeyState.GONE)
    return RunKeyStatus(state, frozenset(key.scopes or ()))


async def run_key_scope(ctx: Any) -> RunKeyScope | None:
    """Reload the run's API key AND resolve its masks and folders now — only for the
    tools that use them. ``None`` when the run was not started by a key.

    Raises :class:`RunKeyRefused` when the key is gone or its scope does not resolve.
    """
    status = await run_key_status(ctx)
    if status is None:
        return None
    if status.state is KeyState.GONE:
        raise RunKeyRefused(KEY_GONE)
    if status.state is KeyState.ORG:
        return RunKeyScope(scoped=False, scopes=status.scopes)
    key = await ctx.session.get(ApiKey, ctx.api_key_id)
    try:
        scope = await resolve_key_scope(ctx.session, key)
    except (KeyScopeError, TooManyAccessMasks) as exc:
        logger.warning("run %s: API key %s scope refused (%s)", getattr(ctx, "run_id", None), ctx.api_key_id, exc)
        raise RunKeyRefused("The API key that started this run no longer has resolvable access.") from exc
    return RunKeyScope(scoped=True, scopes=status.scopes, masks=scope.masks, folder_ids=scope.folder_ids)


async def is_scoped_key_run(ctx: Any) -> bool:
    """Started by a scoped key — or by a key that is gone (most restrictive). Does
    not depend on ``actor_user_id``."""
    status = await run_key_status(ctx)
    return status is not None and status.state is not KeyState.ORG


def ends_run(refusal: str | None) -> bool:
    """Whether a :func:`scoped_key_refusal` means the key is gone and the run must stop."""
    return refusal == KEY_GONE


async def scoped_key_refusal(ctx: Any, tool_name: str) -> str | None:
    """Why this run may not use ``tool_name``, or None if it may.

    :data:`KEY_GONE` for every tool once the run's key is gone (see :func:`ends_run`).
    """
    status = await run_key_status(ctx)
    if status is None:
        return None
    if status.state is KeyState.GONE:
        return KEY_GONE
    if status.state is KeyState.ORG or tool_name in SCOPED_KEY_RUN_TOOLS:
        return None
    return (
        f"'{tool_name}' is not available to a run started through an API key limited to "
        "dimension or folder assignments: it would reach data beyond that key's access."
    )


async def offered_to_run(ctx: Any, specs: list[Any]) -> list[Any]:
    """The tool list this run may be offered."""
    if not await is_scoped_key_run(ctx):
        return specs
    return [s for s in specs if s.name in SCOPED_KEY_RUN_TOOLS]
