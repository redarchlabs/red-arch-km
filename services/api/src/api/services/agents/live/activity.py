"""Carry a background run's transcript out of the process while it is running.

A background agent run is opaque. ``AgentRunExecutor._persist_event`` keeps the
durable record — tool calls, results, usage — but drops every ``delta``, so the
model's actual reasoning never leaves the process. The interactive console has
always streamed those; a delegated run had no way to show them, which is why a
work order with an agent mid-turn reads as "nothing is happening".

A sibling of :mod:`api.services.agents.live.bus` and deliberately not part of it.
That module is a **wake channel**: id-only payloads whose whole point is that
losing one costs nothing because Postgres is the mechanism of record. This is the
opposite — the payload *is* the content, and it is never persisted, so a lost
message is genuinely gone. Sharing a channel would also mean the console's waiter
re-reading Postgres on every token. Different guarantees, different channel names,
different module.

Redis pub/sub rather than an in-process registry, and that is not an optimisation:
the executor runs **inside uvicorn** (``worker/tasks/agents.py`` only POSTs to
``/api/internal/agents/advance-runs``), so an in-memory subscriber map would work
perfectly in dev and silently deliver nothing the moment there are two uvicorn
workers or a second replica.

What is reused from ``bus.py`` and ``workflow/stream.py`` is the discipline: the
org id lives inside the channel name so a channel guessed from another org
resolves to a different string and yields nothing, every channel component is a
UUID, and **every publish failure is swallowed** — nobody watching is not a reason
for a run to fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Deltas arrive one token at a time. Publishing each separately turns a fast model
# into thousands of tiny Redis messages for text a person reads in chunks anyway,
# so they accumulate and flush on this tick.
DELTA_FLUSH_SECONDS = 0.05

# Events worth carrying live. A whitelist, because the emit vocabulary is internal
# and free to grow, and an unrecognised event should not reach a browser.
LIVE_EVENTS = frozenset(
    {"delta", "tool_call", "tool_result", "approval_required", "parked", "usage", "done", "error", "steer"}
)


def run_channel(org_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """Everything one run emits. Distinct from ``bus.run_channel`` on purpose."""
    return f"agent:live:run:{org_id}:{run_id}"


def work_order_channel(org_id: uuid.UUID, work_order_id: uuid.UUID) -> str:
    """Everything every run on one work order emits.

    A work order fans out to several agents, and a page opening one socket per run
    would open and close them as runs come and go. One channel per page keeps the
    socket count at one however many agents are working.
    """
    return f"agent:live:wo:{org_id}:{work_order_id}"


class RunActivityPublisher:
    """Publishes one run's events to its channels.

    Holds no session and never raises: it is bolted onto the executor's ``emit``,
    and a live view nobody is watching must not be able to fail a run.
    """

    def __init__(
        self,
        redis: Any,
        org_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        agent_name: str | None = None,
        work_order_id: uuid.UUID | None = None,
    ) -> None:
        self._redis = redis
        self._agent = agent_name
        self._run_id = run_id
        self._channels = [run_channel(org_id, run_id)]
        if work_order_id is not None:
            self._channels.append(work_order_channel(org_id, work_order_id))
        self._buffer: list[str] = []
        self._flush_task: asyncio.Task[None] | None = None

    async def publish(self, event: dict[str, Any]) -> None:
        """Send one emitted event, coalescing consecutive deltas."""
        kind = str(event.get("type") or "")
        if kind not in LIVE_EVENTS:
            return
        if kind == "delta":
            self._buffer.append(str(event.get("content") or ""))
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_soon())
            return
        # Anything structural ends the current run of text, so the transcript keeps
        # its order: the tokens that led to a tool call are shown before it.
        await self._flush()
        await self._send(event)

    async def close(self) -> None:
        """Flush whatever is buffered, at the end of a turn or a run."""
        task = self._flush_task
        if task is not None and not task.done():
            task.cancel()
        await self._flush()

    async def _flush_soon(self) -> None:
        await asyncio.sleep(DELTA_FLUSH_SECONDS)
        await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        text, self._buffer = "".join(self._buffer), []
        await self._send({"type": "delta", "content": text})

    async def _send(self, event: dict[str, Any]) -> None:
        payload = json.dumps({**event, "run_id": str(self._run_id), "agent": self._agent}, default=str)
        for channel in self._channels:
            try:
                await self._redis.publish(channel, payload)
            except Exception:  # noqa: BLE001 - a live view must never fail the run
                logger.debug("agent activity publish failed", exc_info=True)
