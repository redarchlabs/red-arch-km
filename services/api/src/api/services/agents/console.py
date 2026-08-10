"""Interactive agent console — runs the agent loop inline and streams events.

Bridges the runtime's push-style ``emit`` callback to a pull-style async generator
via a queue, so the SSE endpoint can yield frames as the agent thinks and acts.

**Questions are answered in place.** The operator is present here, so an ASK
verdict auto-approves — and when the agent asks a *question* the stream stays open,
takes the typed answer, and continues the same run in the same turn. If nobody
answers within the inline window, or the browser goes away, the run is handed to
the background sweep exactly as before: the question, the resume state and the
``waiting`` status were all committed before any waiting began, so every way this
can fail degrades to "answer it from the inbox" rather than losing anything.

**A connection is held only while there is work to do.** Each unit — preparing the
run, driving a segment of the loop, polling for an answer, claiming the run back —
takes a session and gives it back. Waiting for a human is not work and owns
nothing. That also removes a subtler hazard: sessions are created with
``expire_on_commit=False``, so an ORM object held across the wait would never
notice that the answer had been written by another session, and the run would
resume from its own stale copy and find no answer at all.

Exactly one party may drive a run. Both this console and the background sweep can
want the same ``queued`` run, so both must win ``AgentRunRepository.claim_run``
first; the loser stands down and says so on the wire.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from api import db_scope
from api.config import Settings
from api.models.agent import Agent
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.services.agents import attachments, lifecycle
from api.services.agents.authority import available_tools
from api.services.agents.delegation import routable_colleagues
from api.services.agents.live import bus
from api.services.agents.llm.catalog import model_supports_vision
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import ToolCallRequest
from api.services.agents.llm.routing import provider_for
from api.services.agents.prompts import build_system_prompt
from api.services.agents.runtime import RunParked, run_agent_loop
from api.services.agents.tools.loader import load_agent_tools
from api.services.agents.tools.spec import ToolContext

logger = logging.getLogger(__name__)

_DONE = object()

# Statuses that mean "someone else owns this run now" — stop, don't resurrect.
_TERMINAL = ("done", "error", "cancelled", "escalated")


@dataclass
class _Segment:
    """What one pass through the agent loop produced."""

    kind: str  # "finished" | "parked" | "failed"
    parked: RunParked | None = None


@dataclass
class _Resume:
    """The state a parked turn continues from, re-read from the database."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    pending: list[ToolCallRequest] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)


class AgentConsoleService:
    def __init__(
        self,
        org_id: uuid.UUID,
        settings: Settings,
        session_factory: async_sessionmaker,
        actor_user_id: uuid.UUID | None,
        redis: Any | None = None,
    ) -> None:
        self._org_id = org_id
        self._settings = settings
        self._factory = session_factory
        self._actor_user_id = actor_user_id
        self._redis = redis

    async def run_stream(
        self,
        agent_id: uuid.UUID,
        history: list[dict[str, Any]],
        *,
        document_ids: list[uuid.UUID] | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        task = asyncio.create_task(self._drive(agent_id, history, emit, queue, document_ids or []))
        try:
            while True:
                event = await queue.get()
                if event is _DONE:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # --- session helper ----------------------------------------------------

    @contextlib.asynccontextmanager
    async def _work(self) -> AsyncGenerator[Any]:
        """A session for one unit of read/write work, scoped and then released.

        Tenant scoping is ``SET LOCAL`` and therefore transaction-scoped, so it is
        re-applied on every acquire — which is also why holding a session across a
        wait would buy nothing: after the commit there is no scope left to keep.
        Stays on ``km_app`` rather than dropping to ``app_user`` because agent
        tools may author ``ce_*`` entity tables; RLS still enforces.
        """
        async with self._factory() as session:
            await db_scope.enter_tenant(session, self._org_id)
            yield session

    # --- the drive ---------------------------------------------------------

    async def _drive(self, agent_id, history, emit, queue, document_ids=None) -> None:
        try:
            prepared = await self._prepare(agent_id, history, emit)
            if prepared is None:
                return
            agent, key, run_id = prepared

            async with self._work() as session:
                reports, advisors = await routable_colleagues(session, self._org_id, agent)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": build_system_prompt(agent, reports=reports, advisors=advisors)},
                *history[:-1],
            ]
            # Vision on arrival: only the last turn carries the image, and only
            # for a model that can see it. See services/agents/attachments.py.
            if history:
                # A session only for this read, then released — the console holds
                # no connection across a stream, which is what keeps a browser tab
                # from pinning one out of a pool of fifteen.
                loaded: list[attachments.Attachment] = []
                if document_ids:
                    async with self._work() as session:
                        loaded = await attachments.load(session, self._org_id, document_ids, self._settings)
                messages.append(
                    attachments.build_user_turn(
                        str(history[-1].get("content") or ""),
                        loaded,
                        vision=model_supports_vision(agent.model),
                    )
                )
            resume = _Resume(messages=messages)
            resumes = 0

            while True:
                segment = await self._run_segment(agent, key, run_id, resume, emit)
                if segment.kind != "parked" or segment.parked is None:
                    return

                parked = segment.parked
                can_inline = resumes < self._settings.agent_console_inline_resumes_max
                await emit(
                    {
                        "type": "waiting",
                        "wait_kind": parked.wait_kind,
                        "run_id": str(run_id),
                        "can_answer_inline": can_inline,
                        **parked.payload,
                    }
                )
                if not can_inline:
                    # An agent that asks endlessly must not hold an HTTP connection
                    # open forever. The question is already in the inbox.
                    await emit({"type": "handed_off", "run_id": str(run_id), "reason": "too many questions"})
                    return

                if not await self._await_answer_and_claim(run_id, emit):
                    await emit({"type": "handed_off", "run_id": str(run_id)})
                    return

                resumed = await self._read_resume(run_id)
                if resumed is None:
                    await emit({"type": "handed_off", "run_id": str(run_id)})
                    return
                resume = resumed
                resumes += 1
        except Exception as exc:  # noqa: BLE001 - never break the SSE contract
            logger.exception("Agent console driver failed")
            await emit({"type": "error", "error": str(exc)})
        finally:
            await queue.put(_DONE)

    async def _prepare(self, agent_id, history, emit) -> tuple[Agent, str, uuid.UUID] | None:
        """Resolve the agent + key and open the run. Releases before returning."""
        async with self._work() as session:
            agent = await AgentRepository(session, self._org_id).get(agent_id)
            if agent is None:
                await emit({"type": "error", "error": "Agent not found"})
                return None
            if not agent.enabled:
                await emit({"type": "error", "error": "Agent is disabled"})
                return None

            key = await resolve_provider_key(session, self._org_id, agent.provider, self._settings)
            if not key:
                await emit({"type": "error", "error": f"No API key configured for provider '{agent.provider}'"})
                return None

            run = await AgentRunRepository(session, self._org_id).create_run(
                agent_id=agent.id,
                provider=agent.provider,
                model=agent.model,
                trigger="manual",
                input={"messages": len(history)},
                actor_user_id=self._actor_user_id,
            )
            await session.commit()
            run_id = run.id

        await emit({"type": "run_started", "run_id": str(run_id)})
        return agent, key, run_id

    async def _run_segment(self, agent: Agent, key: str, run_id: uuid.UUID, resume: _Resume, emit) -> _Segment:
        """Drive the loop until it finishes, parks, or fails. Holds a session —
        the loop is continuous work: every tool call reads or writes."""
        async with self._work() as session:
            run_repo = AgentRunRepository(session, self._org_id)
            run = await run_repo.get_run(run_id)
            if run is None:
                await emit({"type": "error", "error": "Run disappeared"})
                return _Segment("failed")

            # Re-resolved per segment: an MCP server's token may have expired while
            # a question sat unanswered, and the agent's grants may have changed.
            all_specs = await load_agent_tools(
                session, self._org_id, agent, self._settings, actor_user_id=self._actor_user_id
            )
            specs = available_tools(agent, all_specs)
            ctx = ToolContext(
                session=session,
                org_id=self._org_id,
                settings=self._settings,
                agent=agent,
                actor_user_id=self._actor_user_id,
                run_id=run_id,
            )

            async def heartbeat_emit(event: dict[str, Any]) -> None:
                """Stream the event AND renew the run's lease.

                Without this a console run outliving ``agent_run_lease_ttl_seconds``
                looks orphaned to ``_reclaim_stale``, which requeues it — and the
                sweep then drives a second copy in parallel, duplicating every tool
                side effect. The worker path has always heartbeated.
                """
                if event.get("type") in ("tool_call", "tool_result", "usage"):
                    await run_repo.heartbeat(run_id)
                await emit(event)

            params = agent.params or {}
            try:
                result = await run_agent_loop(
                    provider=provider_for(self._settings, agent.model, key),
                    agent=agent,
                    model=agent.model,
                    messages=resume.messages,
                    specs=specs,
                    ctx=ctx,
                    emit=heartbeat_emit,
                    max_iterations=self._settings.agent_max_iterations,
                    temperature=params.get("temperature"),
                    max_tokens=params.get("max_tokens"),
                    reasoning_effort=params.get("reasoning_effort"),
                    # No elision on this path. Eliding a tool result is only safe
                    # where the full one was persisted for read_run_detail to find,
                    # and the console streams its events to a watching human rather
                    # than writing them as run steps. Interactive runs are short and
                    # a person is waiting, so there is little to save here anyway.
                    tool_result_budget=0,
                    resume_tool_calls=resume.pending or None,
                    resume_answers=resume.answers or None,
                )
                await run_repo.add_step(run_id, kind="assistant", content={"content": result.final_content})
                # Clear resume state so a finished run carries no stale turn.
                if isinstance(run.input, dict) and "resume" in run.input:
                    run.input = {k: v for k, v in run.input.items() if k != "resume"}
                await lifecycle.finalize_run(
                    session,
                    self._org_id,
                    run,
                    status="done",
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                )
                await session.commit()
                return _Segment("finished")
            except RunParked as parked:
                # The agent asked something. NOT a failure: the generic handler
                # below would roll back the question row and the notification the
                # handler had just written, then mark the run "error" — losing the
                # question while telling the user it crashed.
                run.input = {
                    **(run.input or {}),
                    "resume": {
                        "messages": parked.messages or resume.messages,
                        "pending": parked.pending or [],
                        "approved": [],
                        # Answers already consumed are baked into ``messages``;
                        # only ones still owed to an unexecuted call carry over.
                        "answers": {
                            k: v
                            for k, v in resume.answers.items()
                            if k in {str(p.get("id")) for p in (parked.pending or [])}
                        },
                    },
                }
                await run_repo.mark_waiting(
                    run,
                    parked.wait_kind,
                    prompt_tokens=parked.prompt_tokens,
                    completion_tokens=parked.completion_tokens,
                    total_tokens=parked.total_tokens,
                )
                await session.commit()
                return _Segment("parked", parked)
            except Exception as exc:  # noqa: BLE001 - report + persist error state
                logger.exception("Agent console run %s failed", run_id)
                await session.rollback()
                await db_scope.enter_tenant(session, self._org_id)  # rollback reset SET LOCAL
                failed = await run_repo.get_run(run_id)
                if failed is not None:
                    await lifecycle.finalize_run(session, self._org_id, failed, status="error", error=str(exc))
                    await session.commit()
                await emit({"type": "error", "error": str(exc)})
                return _Segment("failed")

    # --- waiting for the answer (holds no session) -------------------------

    async def _await_answer_and_claim(self, run_id: uuid.UUID, emit) -> bool:
        """Wait for the question to be settled, then win the right to drive.

        Returns whether THIS console may continue the run. ``False`` means the run
        is untouched and proceeds exactly as it does today — someone else claimed
        it, or nobody answered in time and it stays in the inbox.

        Three things race: a Redis wake (fast path), a Postgres poll (the
        mechanism of record, so a Redis outage costs latency and nothing else),
        and the deadline. No session is held across any of them — each poll takes
        one and gives it straight back.
        """
        deadline = asyncio.get_running_loop().time() + self._settings.agent_console_inline_wait_seconds
        wake: AsyncGenerator[dict[str, Any] | None] | None = None
        if self._redis is not None:
            wake = bus.subscribe(self._redis, bus.run_channel(self._org_id, run_id))

        try:
            # Check once *after* subscribing: an answer that landed between the park
            # commit and the subscription would otherwise publish to nobody, and we
            # would wait a full poll interval for news that already exists.
            claimed = await self._try_claim(run_id)
            if claimed is not None:
                return claimed

            while asyncio.get_running_loop().time() < deadline:
                if wake is not None:
                    with contextlib.suppress(StopAsyncIteration, Exception):
                        await asyncio.wait_for(anext(wake), timeout=_POLL_SECONDS)
                else:
                    await asyncio.sleep(_POLL_SECONDS)

                claimed = await self._try_claim(run_id)
                if claimed is not None:
                    return claimed
                await emit({"type": "ping"})
            return False
        finally:
            if wake is not None:
                with contextlib.suppress(Exception):
                    await wake.aclose()

    async def _try_claim(self, run_id: uuid.UUID) -> bool | None:
        """``None`` = still waiting; ``True`` = we own the run; ``False`` = stand down."""
        async with self._work() as session:
            repo = AgentRunRepository(session, self._org_id)
            status = await repo.current_status(run_id)
            if status == "waiting" or status is None:
                return None
            if status in _TERMINAL:
                return False
            if status == "queued":
                run = await repo.claim_run(run_id)
                await session.commit()
                return run is not None
            # "running": the sweep got there first and is driving it.
            return False

    async def _read_resume(self, run_id: uuid.UUID) -> _Resume | None:
        """Re-read the turn to continue from — from the database, never memory.

        The answer was written by a different session. With
        ``expire_on_commit=False`` an in-memory copy would never learn about it, so
        the run would resume and find its own question still unanswered.
        """
        async with self._work() as session:
            run = await AgentRunRepository(session, self._org_id).get_run(run_id)
            if run is None or not isinstance(run.input, dict):
                return None
            state = run.input.get("resume") or {}
            return _Resume(
                messages=list(state.get("messages") or []),
                pending=[ToolCallRequest(**tc) for tc in state.get("pending") or []],
                answers=dict(state.get("answers") or {}),
            )


# How often to ask Postgres whether the question has been settled. The Redis wake
# usually beats it; this is the floor on how long a resume can take without it.
_POLL_SECONDS = 2.0
