"""The provider-agnostic agent tool-calling loop.

Generalizes the single config assistant (``api.services.agent``) into a reusable
engine driven by any :class:`LLMProvider`. The loop streams a turn, records the
assistant message, gates each requested tool through the authority engine, then
executes and feeds results back, repeating until the model stops calling tools or
the iteration budget is exhausted.

Two-phase tool handling makes human approval safe to *suspend and resume*: every
tool call in a turn is authority-gated **before any of them executes**, so a run
that parks on an ASK verdict has taken no partial side effects. The messages and
pending tool calls are attached to :class:`RunParked` so the worker can persist
resume state and continue the exact same turn after a human approves.

I/O is injected so one loop serves both runtimes:
* ``emit`` — an async sink for typed events (SSE frames and/or step persistence).
* ``approval_strategy`` — what an ASK verdict does: approve inline (console) or
  record an approval and raise :class:`RunParked` (worker).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.llm.provider import Completion, LLMError, LLMProvider, TextDelta, ToolCallRequest
from api.services.agents.tools.spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
# (spec, arguments) -> approved? ; may raise RunParked to suspend the run.
ApprovalStrategy = Callable[[ToolSpec, dict[str, Any]], Awaitable[bool]]


class RunParked(Exception):  # noqa: N818 - a control signal, not an error condition
    """Raised by an approval strategy to suspend the run for later resume.

    ``messages`` + ``pending`` are populated by the loop before propagation so the
    caller can persist resume state and continue the same turn after approval.
    """

    def __init__(self, wait_kind: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(f"run parked: {wait_kind}")
        self.wait_kind = wait_kind
        self.payload = payload or {}
        self.messages: list[dict[str, Any]] | None = None
        self.pending: list[dict[str, Any]] | None = None


@dataclass
class RunResult:
    final_content: str = ""
    truncated: bool = False
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)


async def _approve_inline(_spec: ToolSpec, _args: dict[str, Any]) -> bool:
    """Default strategy for the interactive console: the operator is present."""
    return True


def _assistant_message(completion: Completion) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": completion.content or ""}
    if completion.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in completion.tool_calls
        ]
    return msg


def _tool_message(tc: ToolCallRequest, output: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(output, default=str)}


async def run_agent_loop(
    *,
    provider: LLMProvider,
    agent: Agent,
    model: str,
    messages: list[dict[str, Any]],
    specs: list[ToolSpec],
    ctx: ToolContext,
    emit: Emit,
    max_iterations: int,
    temperature: float | None = None,
    max_tokens: int | None = None,
    approval_strategy: ApprovalStrategy = _approve_inline,
    resume_tool_calls: list[ToolCallRequest] | None = None,
) -> RunResult:
    """Drive ``agent`` to quiescence. ``messages`` already includes the system prompt.

    ``resume_tool_calls`` continues a parked turn: the loop skips the initial stream
    and gates/executes those calls first (``messages`` must already end with the
    assistant message that requested them).
    """
    specs_by_name = {s.name: s for s in specs}
    schemas = [s.openai_schema() for s in specs] or None
    result = RunResult(messages=messages)
    pending = resume_tool_calls

    for _iteration in range(max_iterations):
        if pending is not None:
            tool_calls = pending
            pending = None
        else:
            completion = await _stream_turn(
                provider, model, messages, schemas, temperature, max_tokens, emit
            )
            if completion.usage:
                result.prompt_tokens += completion.usage.prompt_tokens
                result.completion_tokens += completion.usage.completion_tokens
                result.total_tokens += completion.usage.total_tokens
                await emit(
                    {
                        "type": "usage",
                        "prompt_tokens": completion.usage.prompt_tokens,
                        "completion_tokens": completion.usage.completion_tokens,
                        "total_tokens": completion.usage.total_tokens,
                    }
                )
            messages.append(_assistant_message(completion))
            if not completion.tool_calls:
                result.final_content = completion.content or ""
                await emit({"type": "done"})
                return result
            tool_calls = list(completion.tool_calls)

        # Phase 1: authority-gate every call BEFORE any executes (parks cleanly).
        plans = await _gate_tools(agent, tool_calls, specs_by_name, emit, approval_strategy, messages)
        # Phase 2: execute (or return the pre-set deny/error output).
        for tc, spec, preset in plans:
            result.tool_calls += 1
            output = preset if preset is not None else await _run_tool(spec, ctx, tc)
            await emit({"type": "tool_result", "name": tc.name, "result": output})
            messages.append(_tool_message(tc, output))

    result.truncated = True
    await emit({"type": "done", "truncated": True})
    return result


async def _stream_turn(provider, model, messages, schemas, temperature, max_tokens, emit) -> Completion:
    completion: Completion | None = None
    try:
        async for event in provider.stream(
            model=model, messages=messages, tools=schemas,
            temperature=temperature, max_tokens=max_tokens,
        ):
            if isinstance(event, TextDelta):
                await emit({"type": "delta", "content": event.text})
            elif isinstance(event, Completion):
                completion = event
    except LLMError as exc:
        await emit({"type": "error", "error": str(exc)})
        raise
    assert completion is not None  # provider always yields a terminal Completion
    return completion


async def _gate_tools(agent, tool_calls, specs_by_name, emit, approval_strategy, messages):
    """Return a plan of (tc, spec, preset_output|None). Raises RunParked to suspend."""
    plans: list[tuple[ToolCallRequest, ToolSpec | None, dict[str, Any] | None]] = []
    for tc in tool_calls:
        await emit({"type": "tool_call", "id": tc.id, "name": tc.name, "arguments": tc.arguments})
        spec = specs_by_name.get(tc.name)
        if spec is None:
            plans.append((tc, None, {"error": f"Unknown tool: {tc.name}"}))
            continue
        verdict = decide(agent, spec)
        if verdict.decision is Decision.DENY:
            plans.append((tc, spec, {"error": f"Not permitted: {verdict.reason}"}))
            continue
        if verdict.decision is Decision.ASK:
            await emit({"type": "approval_required", "name": tc.name, "arguments": tc.arguments})
            try:
                approved = await approval_strategy(spec, tc.arguments)
            except RunParked as parked:
                parked.messages = messages
                parked.pending = [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in tool_calls]
                raise
            if not approved:
                plans.append((tc, spec, {"error": "Denied by human reviewer"}))
                continue
        plans.append((tc, spec, None))
    return plans


async def _run_tool(spec: ToolSpec, ctx: ToolContext, tc: ToolCallRequest) -> dict[str, Any]:
    try:
        return await spec.handler(ctx, tc.arguments)
    except Exception as exc:  # noqa: BLE001 - a tool failure must not kill the run
        logger.exception("Agent tool %s failed", tc.name)
        return {"error": f"Tool '{tc.name}' failed: {exc}"}
