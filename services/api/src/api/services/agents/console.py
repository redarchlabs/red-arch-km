"""Interactive agent console — runs the agent loop inline and streams events.

Bridges the runtime's push-style ``emit`` callback to a pull-style async generator
via a queue, so the SSE endpoint can yield frames as the agent thinks and acts.
Uses a privileged session with explicit org scoping in every repo (matching the
config assistant and the workflows run endpoint, which drives its own tenant
scoping inside ``execute_workflow_run``).

The operator is present here, so an ASK verdict auto-approves — but a *question*
(``ask_human``/``consult_peer``) still parks, because an answer takes as long as a
person takes and SSE cannot hold a request open that long. A parked console run is
therefore **handed to the worker**: the question is committed, the run is left
``waiting``, and answering it from the inbox resumes it in the background. The
transcript is durable, so the console can show the outcome afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from api import db_scope
from api.config import Settings
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.services.agents import lifecycle
from api.services.agents.authority import available_tools
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMProvider
from api.services.agents.prompts import build_system_prompt
from api.services.agents.runtime import RunParked, run_agent_loop
from api.services.agents.tools.loader import load_agent_tools
from api.services.agents.tools.spec import ToolContext

logger = logging.getLogger(__name__)

_DONE = object()


class AgentConsoleService:
    def __init__(
        self,
        org_id: uuid.UUID,
        settings: Settings,
        session_factory: async_sessionmaker,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        self._org_id = org_id
        self._settings = settings
        self._factory = session_factory
        self._actor_user_id = actor_user_id

    async def run_stream(self, agent_id: uuid.UUID, history: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        task = asyncio.create_task(self._drive(agent_id, history, emit, queue))
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

    async def _drive(self, agent_id, history, emit, queue) -> None:
        try:
            async with self._factory() as session:
                # Scope to the console's org, staying on km_app so agent tools that
                # author ce_* entity tables can DDL. RLS is a real backstop. Re-set
                # after each commit below (SET LOCAL resets on commit). See db_scope.
                await db_scope.enter_tenant(session, self._org_id)
                agent = await AgentRepository(session, self._org_id).get(agent_id)
                if agent is None:
                    await emit({"type": "error", "error": "Agent not found"})
                    return
                if not agent.enabled:
                    await emit({"type": "error", "error": "Agent is disabled"})
                    return

                key = await resolve_provider_key(session, self._org_id, agent.provider, self._settings)
                if not key:
                    await emit({"type": "error", "error": f"No API key configured for provider '{agent.provider}'"})
                    return

                run_repo = AgentRunRepository(session, self._org_id)
                run = await run_repo.create_run(
                    agent_id=agent.id,
                    provider=agent.provider,
                    model=agent.model,
                    trigger="manual",
                    input={"messages": len(history)},
                    actor_user_id=self._actor_user_id,
                )
                await session.commit()
                await db_scope.enter_tenant(session, self._org_id)  # re-scope: commit reset SET LOCAL
                await emit({"type": "run_started", "run_id": str(run.id)})

                provider = LLMProvider(api_key=key)
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
                    run_id=run.id,
                )
                params = agent.params or {}
                messages = [{"role": "system", "content": build_system_prompt(agent)}, *history]

                async def heartbeat_emit(event: dict[str, Any]) -> None:
                    """Stream the event AND renew the run's lease.

                    Without this, a console run that takes longer than
                    ``agent_run_lease_ttl_seconds`` looks orphaned to
                    ``_reclaim_stale``, which requeues it — and the sweep then drives
                    a second copy in parallel with this one, duplicating every tool
                    side effect. The worker path has always heartbeated (see
                    ``AgentRunExecutor._persist_event``); the console never did.
                    """
                    if event.get("type") in ("tool_call", "tool_result", "usage"):
                        await run_repo.heartbeat(run.id)
                    await emit(event)

                try:
                    result = await run_agent_loop(
                        provider=provider,
                        agent=agent,
                        model=agent.model,
                        messages=messages,
                        specs=specs,
                        ctx=ctx,
                        emit=heartbeat_emit,
                        max_iterations=self._settings.agent_max_iterations,
                        temperature=params.get("temperature"),
                        max_tokens=params.get("max_tokens"),
                    )
                    await run_repo.add_step(run.id, kind="assistant", content={"content": result.final_content})
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
                except RunParked as parked:
                    # The agent asked something and is waiting on the answer. This is
                    # NOT a failure: falling through to the handler below would roll
                    # back the question row and the notification that were written
                    # inside the handler, then mark the run "error" — losing the
                    # question entirely while telling the user it crashed.
                    run.input = {
                        **(run.input or {}),
                        "resume": {
                            "messages": parked.messages or messages,
                            "pending": parked.pending or [],
                            "approved": [],
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
                    await emit(
                        {
                            "type": "waiting",
                            "wait_kind": parked.wait_kind,
                            "run_id": str(run.id),
                            **parked.payload,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - report + persist error state
                    logger.exception("Agent console run %s failed", run.id)
                    await session.rollback()
                    await db_scope.enter_tenant(session, self._org_id)  # rollback reset SET LOCAL
                    failed = await run_repo.get_run(run.id)
                    if failed is not None:
                        await lifecycle.finalize_run(session, self._org_id, failed, status="error", error=str(exc))
                        await session.commit()
                    await emit({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never break the SSE contract
            logger.exception("Agent console driver failed")
            await emit({"type": "error", "error": str(exc)})
        finally:
            await queue.put(_DONE)
