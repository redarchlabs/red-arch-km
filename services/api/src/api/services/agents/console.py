"""Interactive agent console — runs the agent loop inline and streams events.

Bridges the runtime's push-style ``emit`` callback to a pull-style async generator
via a queue, so the SSE endpoint can yield frames as the agent thinks and acts.
Uses a privileged session with explicit org scoping in every repo (matching the
config assistant and the workflows run endpoint, which drives its own tenant
scoping inside ``execute_workflow_run``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import Settings
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.services.agents.authority import available_tools
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMProvider
from api.services.agents.prompts import build_system_prompt
from api.services.agents.runtime import run_agent_loop
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

    async def run_stream(
        self, agent_id: uuid.UUID, history: list[dict[str, Any]]
    ) -> AsyncGenerator[dict[str, Any]]:
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
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _drive(self, agent_id, history, emit, queue) -> None:
        try:
            async with self._factory() as session:
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
                    agent_id=agent.id, provider=agent.provider, model=agent.model,
                    trigger="manual", input={"messages": len(history)}, actor_user_id=self._actor_user_id,
                )
                await session.commit()
                await emit({"type": "run_started", "run_id": str(run.id)})

                provider = LLMProvider(api_key=key)
                all_specs = await load_agent_tools(session, self._org_id, agent, self._settings)
                specs = available_tools(agent, all_specs)
                ctx = ToolContext(
                    session=session, org_id=self._org_id, settings=self._settings,
                    agent=agent, actor_user_id=self._actor_user_id, run_id=run.id,
                )
                params = agent.params or {}
                messages = [{"role": "system", "content": build_system_prompt(agent)}, *history]
                try:
                    result = await run_agent_loop(
                        provider=provider, agent=agent, model=agent.model, messages=messages,
                        specs=specs, ctx=ctx, emit=emit,
                        max_iterations=self._settings.agent_max_iterations,
                        temperature=params.get("temperature"), max_tokens=params.get("max_tokens"),
                    )
                    await run_repo.add_step(run.id, kind="assistant", content={"content": result.final_content})
                    await run_repo.finalize_run(
                        run, status="done", prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
                    )
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 - report + persist error state
                    logger.exception("Agent console run %s failed", run.id)
                    await session.rollback()
                    failed = await run_repo.get_run(run.id)
                    if failed is not None:
                        await run_repo.finalize_run(failed, status="error", error=str(exc))
                        await session.commit()
                    await emit({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never break the SSE contract
            logger.exception("Agent console driver failed")
            await emit({"type": "error", "error": str(exc)})
        finally:
            await queue.put(_DONE)
