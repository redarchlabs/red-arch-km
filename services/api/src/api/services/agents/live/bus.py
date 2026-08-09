"""A wake channel for one agent run — Redis pub/sub as a latency shortcut.

When a console parks a run on a question, the answer arrives through a *different*
HTTP request, quite possibly in a different process. The console needs to notice.
It could poll Postgres, and it does — that is the mechanism this is layered over.
This channel just makes the common case fast: an answer typed into the console
resumes the run in milliseconds instead of on the next poll tick.

Two properties keep that safe to rely on, and both are deliberate:

* **The payload is an id, never the answer itself.** A wake says "something
  changed on this run"; the recipient re-reads Postgres to find out what. So a
  message that is lost, duplicated, or delivered late changes nothing — the poll
  reaches the same conclusion a moment later.
* **Every failure is swallowed.** Redis being down degrades resume latency from
  milliseconds to one poll interval. It must never fail a run.

The channel name embeds the org and is built by each side from its own context,
so a run id from another org resolves to a channel nobody publishes on — the same
authz trick :mod:`api.services.workflow.stream` relies on. Both components are
UUIDs, so caller text can never reach a channel name.

This is a sibling of that module rather than a generalisation of it: the workflow
stream is a short-lived token feed that terminates on ``done`` and renders SSE
bytes, while this is an id-only wake consumed in-process by a coroutine that
never renders anything. Merging them would mean a function with a flag for every
one of those differences.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)

# The only event today: a question on this run was settled (answered or declined).
EVENT_ANSWER = "answer"


def run_channel(org_id: uuid.UUID | str, run_id: uuid.UUID | str) -> str:
    """Wake channel for one run, namespaced by org."""
    return f"agent:run:{org_id}:{run_id}"


def is_valid_id(value: Any) -> bool:
    """Whether ``value`` is a well-formed channel component (a UUID)."""
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


async def publish_run_event(
    redis: Any,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """Nudge whoever is waiting on ``run_id``. Never raises.

    A publish that fails, or that nobody is subscribed to, is not an error: the
    waiter's Postgres poll is the mechanism of record and will pick the change up
    regardless.
    """
    try:
        await redis.publish(run_channel(org_id, run_id), json.dumps(payload))
    except Exception:  # noqa: BLE001 - a wake must never fail the caller's request
        logger.debug("agent run wake publish failed", exc_info=True)


async def subscribe(
    redis: Any,
    channel: str,
    *,
    idle_timeout: float = 1.0,
) -> AsyncGenerator[dict[str, Any] | None]:
    """Yield decoded payloads on ``channel``; yield ``None`` on each idle tick.

    Idle ticks are yielded rather than swallowed so the caller owns its own
    deadline and keepalive policy — this module has no opinion about how long
    anyone is willing to wait. Unparseable payloads are skipped rather than
    forwarded, so a stray publisher cannot corrupt a consumer's contract.

    The caller must close the generator (``aclose``) to release the subscription;
    an ``async for`` that breaks out does so automatically.
    """
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(channel)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=idle_timeout)
            if message is None:
                yield None
                continue
            raw = message.get("data")
            try:
                payload = json.loads(raw if isinstance(raw, str) else str(raw or ""))
            except (ValueError, TypeError):
                continue
            yield payload if isinstance(payload, dict) else None
    except asyncio.CancelledError:  # the waiter gave up — end quietly
        raise
    except Exception:  # noqa: BLE001 - degrade to the caller's poll, never raise
        logger.debug("agent run wake subscription failed", exc_info=True)
    finally:
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 - teardown must not raise into the caller
            logger.debug("agent run wake teardown failed", exc_info=True)
