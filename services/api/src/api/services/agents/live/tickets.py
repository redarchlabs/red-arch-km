"""Short-lived tickets that let a browser open an authenticated WebSocket.

A browser cannot set headers on a WebSocket, so the Clerk bearer token the rest of
the API uses is unavailable at connect time. Putting it in the query string would
work and is the wrong trade: a URL lands in access logs, proxy logs and browser
history, and that token is long-lived and carries the user's whole session.

Instead the page asks for a ticket over an ordinary authenticated request, and
spends it immediately. A ticket is opaque, single-use (consumed with ``GETDEL``),
scoped to one org and one profile, and expires in a minute — so a leaked one is
worth almost nothing, and a stolen one is worth nothing twice.

Redis rather than a table: it expires itself, needs no migration, and this is
exactly the lifetime Redis is good at. The opaque-value discipline is the same as
:mod:`api.services.form_token` — the value is never an identifier, only a secret.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Long enough to click through a page load, short enough that a ticket found in a
# log is already dead.
TICKET_TTL_SECONDS = 60

# 32 bytes -> ~43 url-safe chars, the same budget as a public form link.
_TICKET_BYTES = 32

_PREFIX = "agent:live:ticket:"


@dataclass(frozen=True, slots=True)
class Ticket:
    """Who the socket may act as. Read from Redis, never from the client."""

    org_id: uuid.UUID
    profile_id: uuid.UUID | None


def _key(raw: str) -> str:
    return f"{_PREFIX}{raw}"


async def mint(redis: Any, *, org_id: uuid.UUID, profile_id: uuid.UUID | None) -> str:
    """Issue a ticket for this org + user. Returns the raw value, shown once."""
    raw = secrets.token_urlsafe(_TICKET_BYTES)
    await redis.set(
        _key(raw),
        json.dumps({"org_id": str(org_id), "profile_id": str(profile_id) if profile_id else None}),
        ex=TICKET_TTL_SECONDS,
    )
    return raw


async def consume(redis: Any, raw: str) -> Ticket | None:
    """Spend a ticket. ``None`` if it is unknown, already used, or expired.

    ``GETDEL`` is the single-use guarantee: two sockets racing the same ticket
    cannot both win, because only one of them gets a value back.
    """
    if not raw:
        return None
    try:
        payload = await redis.getdel(_key(raw))
    except Exception:  # noqa: BLE001 - Redis down means no live view, not a 500
        logger.debug("agent live ticket lookup failed", exc_info=True)
        return None
    if not payload:
        return None
    try:
        data = json.loads(payload)
        profile = data.get("profile_id")
        return Ticket(
            org_id=uuid.UUID(str(data["org_id"])),
            profile_id=uuid.UUID(str(profile)) if profile else None,
        )
    except (ValueError, KeyError, TypeError):
        return None
