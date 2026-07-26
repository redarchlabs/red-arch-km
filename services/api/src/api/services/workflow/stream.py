"""Live token stream for a workflow run.

A workflow-driven chat (the ``chat`` view element) fires an answer workflow and
waits for the reply record to appear. The LLM step inside that run generates for
many seconds, so nothing can be shown until the whole run finishes. This module
carries that step's tokens out of the run while it is still executing:

    caller mints a token  ->  subscribes to the channel  ->  runs the workflow
                                        ^                          |
                                        +---- deltas published -----+

The channel name embeds the ORG, and a subscriber builds it from its own request
context — so a token guessed or stolen from another org resolves to a different
channel and yields nothing. The token itself is caller-supplied and opaque; it is
never trusted as an identifier, only as a random suffix.

Pub/sub is deliberately lossy: nothing is persisted, a delta published with no
subscriber is dropped, and every publish failure is swallowed. The reply record
written by the run remains the source of truth — this is a preview, and losing it
degrades the UI to the old poll-and-wait behaviour rather than breaking a run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)

# Event names on the wire. `delta` carries one token; `done` closes the stream.
EVENT_DELTA = "delta"
EVENT_DONE = "done"

# A stream is short-lived (one chat turn). Subscribers that outlive the run are
# closed by this cap so a forgotten browser tab can't hold a connection forever.
STREAM_TIMEOUT_SECONDS = 180


def channel_for(org_id: uuid.UUID, token: str) -> str:
    """Redis channel carrying one run's deltas, namespaced by org."""
    return f"wf:stream:{org_id}:{token}"


def is_valid_token(token: str) -> bool:
    """Whether ``token`` is a well-formed stream token (a UUID).

    Constraining the shape keeps caller-supplied text out of the channel name and
    guarantees enough entropy that a channel can't be guessed.
    """
    try:
        uuid.UUID(str(token))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class RunStreamPublisher:
    """Publishes one run's LLM tokens to its channel. Failures are swallowed."""

    def __init__(self, redis: Any, org_id: uuid.UUID, token: str) -> None:
        self._redis = redis
        self._channel = channel_for(org_id, token)

    @property
    def channel(self) -> str:
        return self._channel

    async def _publish(self, payload: dict[str, Any]) -> None:
        try:
            await self._redis.publish(self._channel, json.dumps(payload))
        except Exception:  # noqa: BLE001 — a preview must never fail the run
            logger.debug("run stream publish failed", exc_info=True)

    async def delta(self, text: str) -> None:
        """Emit one generated token."""
        if text:
            await self._publish({"type": EVENT_DELTA, "text": text})

    async def done(self) -> None:
        """Signal that no more tokens are coming, so subscribers can close."""
        await self._publish({"type": EVENT_DONE})


async def sse_frames(redis: Any, channel: str) -> AsyncGenerator[bytes]:
    """Yield SSE frames for ``channel`` until the run says ``done`` or time runs out.

    Idle ticks emit a comment frame so proxies don't drop a connection while the
    run is still retrieving. Anything unparseable on the channel is skipped rather
    than forwarded, so a stray publisher can't corrupt the frame contract.
    """
    pubsub = redis.pubsub()
    loop = asyncio.get_running_loop()
    try:
        await pubsub.subscribe(channel)
        deadline = loop.time() + STREAM_TIMEOUT_SECONDS
        while loop.time() < deadline:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                yield b": keepalive\n\n"
                continue
            raw = message.get("data")
            payload = raw if isinstance(raw, str) else str(raw or "")
            try:
                event = str(json.loads(payload).get("type") or EVENT_DELTA)
            except (ValueError, AttributeError):
                continue
            yield f"event: {event}\ndata: {payload}\n\n".encode()
            if event == EVENT_DONE:
                return
        yield b'event: done\ndata: {"timeout": true}\n\n'
    except asyncio.CancelledError:  # client disconnected — end quietly
        raise
    except Exception:  # noqa: BLE001 - never break the SSE frame contract
        logger.debug("run token stream failed", exc_info=True)
        yield b'event: error\ndata: {"detail": "stream failed"}\n\n'
    finally:
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 - teardown must not raise into the response
            logger.debug("run token stream teardown failed", exc_info=True)
