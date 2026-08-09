"""Watch an agent work, and say something to it while it does.

A WebSocket rather than SSE because this is two-way: the same connection carries
the run's transcript out and a person's interjection back in.

Two rules shape the implementation, and both are about the connection pool:

* **The socket holds no database session.** The pool is 15 for the whole API
  *process* — the same process the run executor runs in — so a connection pinned
  per open tab starves background agent runs, not just the UI. A session is opened
  only to write a steer, and released immediately. This is the same reasoning that
  produced :func:`require_org_access_streaming`.
* **Fan-out is Redis, not memory.** The executor runs inside uvicorn, so an
  in-process subscriber registry would work in dev and deliver nothing the moment
  there are two workers or a second replica.

Authentication is a **ticket**, because a browser cannot set headers on a
WebSocket and a Clerk bearer token has no business in a URL. See
:mod:`api.services.agents.live.tickets`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api import db_scope
from api.auth.dependencies import OrgContext, require_org_access_streaming
from api.config import Settings, get_settings
from api.db import get_session_factory
from api.dependencies import get_redis_client
from api.repositories.agent_run import AgentRunRepository
from api.repositories.agent_run_messages import AgentRunMessageRepository
from api.services.agents.live import activity, tickets

logger = logging.getLogger(__name__)

router = APIRouter()

# A run in one of these states can still act on what you tell it. A terminal run
# cannot: resurrecting one would strand its finalize and any workflow waiting on it.
_STEERABLE = ("queued", "running", "waiting")

# How long a socket may sit with nothing happening before it is closed. A page left
# open overnight should not hold a connection to Redis forever.
IDLE_TIMEOUT_SECONDS = 1800

# Room for a correction, not for a new work order.
MAX_STEER_CHARS = 4000

# WebSocket close codes. 4401 and 4403 mirror HTTP 401/403 in the application range.
_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_FORBIDDEN = 4403


class TicketResponse(BaseModel):
    ticket: str
    expires_in: int


@router.post("/live/ticket", response_model=TicketResponse)
async def mint_ticket(
    # The streaming variant deliberately: this is a one-millisecond lookup and must
    # not hold a pooled connection while FastAPI finishes the response.
    ctx: Annotated[OrgContext, Depends(require_org_access_streaming)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TicketResponse:
    """Mint a short-lived, single-use ticket for opening a live socket."""
    raw = await tickets.mint(get_redis_client(settings), org_id=ctx.org_id, profile_id=ctx.user.profile_id)
    return TicketResponse(ticket=raw, expires_in=tickets.TICKET_TTL_SECONDS)


async def _record_steer(
    settings: Settings,
    org_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    run_id: uuid.UUID,
    text: str,
    document_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Queue a steer for ``run_id``. Opens a session, writes, releases.

    Returns the frame to send back. Acknowledged honestly as *queued*: the run
    picks it up at the top of its next turn, and nothing here aborts an in-flight
    stream to deliver sooner.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        await db_scope.enter_tenant(session, org_id)
        run = await AgentRunRepository(session, org_id).get_run(run_id)
        if run is None:
            return {"type": "steer_rejected", "run_id": str(run_id), "reason": "run not found"}
        if run.status not in _STEERABLE:
            return {
                "type": "steer_rejected",
                "run_id": str(run_id),
                "reason": f"that run is already {run.status}",
            }
        # Attachments ride on the work order, not the steer row: the document is a
        # durable part of the order's record, and the message only has to name it.
        body = text
        if document_ids and run.work_order_id is not None:
            from api.services.agents.work_order_service import WorkOrderService

            attached = await WorkOrderService(session, org_id).attach_documents(
                run.work_order_id, document_ids, kind="input", actor_profile_id=profile_id
            )
            names = "\n".join(f"[attached: {a.filename}]" for a in attached)
            body = "\n".join(part for part in (text, names) if part)
        await AgentRunMessageRepository(session, org_id).add(run_id, body, sent_by_profile_id=profile_id)
        await session.commit()
    return {"type": "steer_queued", "run_id": str(run_id), "when": "next turn"}


@router.websocket("/live/ws")
async def live_socket(
    websocket: WebSocket,
    ticket: str = "",
    work_order_id: str = "",
    run_id: str = "",
) -> None:
    """Stream one work order's (or one run's) activity, and accept steers.

    The channel name is built from the org on the *ticket*, never from anything the
    client says — so a run id belonging to another org resolves to a channel nobody
    publishes on, which is the same authz trick the workflow stream relies on.
    """
    settings = get_settings()
    redis = get_redis_client(settings)

    granted = await tickets.consume(redis, ticket)
    if granted is None:
        await websocket.close(code=_CLOSE_UNAUTHENTICATED, reason="invalid or expired ticket")
        return

    scope_id = work_order_id or run_id
    if not activity_scope_is_valid(scope_id):
        await websocket.close(code=_CLOSE_FORBIDDEN, reason="a work_order_id or run_id is required")
        return

    channel = (
        activity.work_order_channel(granted.org_id, uuid.UUID(work_order_id))
        if work_order_id
        else activity.run_channel(granted.org_id, uuid.UUID(run_id))
    )

    await websocket.accept()
    stop = asyncio.Event()

    async def pump() -> None:
        """Redis -> browser. Payloads are forwarded verbatim; re-encoding them
        would only add a chance to corrupt what the publisher already framed."""
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            while not stop.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                raw = message.get("data")
                await websocket.send_text(raw if isinstance(raw, str) else str(raw or ""))
        except (asyncio.CancelledError, WebSocketDisconnect):
            raise
        except Exception:  # noqa: BLE001 - losing the feed closes the socket, nothing more
            logger.debug("live socket pump failed", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
            except TimeoutError:
                # Nothing in either direction for half an hour: close rather than
                # hold a Redis subscription for a tab nobody is looking at.
                break
            frame = _parse(raw)
            if frame is None:
                continue
            if frame.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue
            if frame.get("type") != "steer":
                continue
            text = str(frame.get("text") or "").strip()[:MAX_STEER_CHARS]
            target = str(frame.get("run_id") or run_id)
            documents = _document_ids(frame)
            if (not text and not documents) or not activity_scope_is_valid(target):
                await websocket.send_text(
                    json.dumps({"type": "steer_rejected", "reason": "a run_id and text are required"})
                )
                continue
            reply = await _record_steer(settings, granted.org_id, granted.profile_id, uuid.UUID(target), text)
            await websocket.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump_task


def activity_scope_is_valid(value: str) -> bool:
    """Whether ``value`` is a UUID, so client text never reaches a channel name."""
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _document_ids(frame: dict[str, Any]) -> list[uuid.UUID]:
    """Attachment ids off a steer frame, ignoring anything malformed.

    A bad id must not lose the message someone typed alongside it.
    """
    out: list[uuid.UUID] = []
    for value in frame.get("document_ids") or []:
        try:
            out.append(uuid.UUID(str(value)))
        except (ValueError, TypeError):
            continue
    return out


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        frame = json.loads(raw)
    except ValueError:
        return None
    return frame if isinstance(frame, dict) else None
