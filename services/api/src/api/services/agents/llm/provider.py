"""Provider-agnostic streaming chat/tool-calling over LiteLLM.

LiteLLM normalizes Anthropic / OpenAI / Gemini (and 100+ providers) onto the
OpenAI chat + tool-calling shape, so the JSON tool schemas the runtime already
uses carry across providers unchanged. This wrapper adds two things on top:

* a small, typed event stream (:class:`TextDelta` … then one :class:`Completion`)
  so the runtime can forward text to the SSE console live and act on the fully
  assembled tool calls at turn end;
* provider-error normalization to :class:`LLMError` so callers never see a raw
  vendor exception.

The ``litellm`` import is lazy (via :func:`_litellm`) so pure helpers and the
catalog stay importable — and unit-testable — without the heavy dependency.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# --- Event types ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A chunk of assistant text as it streams."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A fully-assembled tool call the model wants to make."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Completion:
    """Terminal event: the assembled assistant message for this turn."""

    content: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    finish_reason: str | None = None
    usage: Usage | None = None


LLMStreamEvent = TextDelta | Completion


class LLMError(RuntimeError):
    """A provider call failed (auth, rate limit, unknown model, network)."""


# --- Pure helpers (no litellm needed) --------------------------------------


def _extract_usage(chunk: Any) -> Usage | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    return Usage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _accumulate_tool_call(acc: dict[int, dict[str, Any]], delta_tc: Any) -> None:
    """Fold a streamed tool-call delta into the per-index accumulator.

    Providers stream tool calls piecewise: the id + name arrive first, then the
    ``function.arguments`` JSON string in fragments, all sharing an ``index``.
    """
    idx = int(getattr(delta_tc, "index", 0) or 0)
    slot = acc.setdefault(idx, {"id": None, "name": None, "arguments": ""})
    tc_id = getattr(delta_tc, "id", None)
    if tc_id:
        slot["id"] = tc_id
    fn = getattr(delta_tc, "function", None)
    if fn is not None:
        name = getattr(fn, "name", None)
        if name:
            slot["name"] = name
        args = getattr(fn, "arguments", None)
        if args:
            slot["arguments"] += args


def _finalize_tool_calls(acc: dict[int, dict[str, Any]]) -> list[ToolCallRequest]:
    calls: list[ToolCallRequest] = []
    for idx in sorted(acc):
        slot = acc[idx]
        name = slot.get("name")
        if not name:
            continue
        raw = (slot.get("arguments") or "").strip()
        try:
            arguments = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            logger.warning("Discarding un-parseable tool arguments for %s", name)
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCallRequest(id=slot.get("id") or f"call_{idx}", name=name, arguments=arguments)
        )
    return calls


def _litellm() -> Any:
    """Import LiteLLM lazily. Patched in unit tests to avoid the real dependency."""
    import litellm  # noqa: PLC0415 - deliberately lazy

    return litellm


# --- Provider ---------------------------------------------------------------


@dataclass
class LLMProvider:
    """One provider call context. Cheap to construct — build one per run with the
    resolved (org-preferred, central-fallback) API key."""

    api_key: str | None = None
    timeout: float = 120.0
    # Extra kwargs merged into every acompletion call (e.g. api_base for proxies).
    default_params: dict[str, Any] = field(default_factory=dict)

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream one assistant turn. Yields ``TextDelta``s then one ``Completion``."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": self.timeout,
            **self.default_params,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if extra:
            kwargs.update(extra)

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: Usage | None = None

        try:
            response = await _litellm().acompletion(**kwargs)
            async for chunk in response:
                usage = _extract_usage(chunk) or usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                text = getattr(delta, "content", None)
                if text:
                    content_parts.append(text)
                    yield TextDelta(text=text)
                for delta_tc in getattr(delta, "tool_calls", None) or []:
                    _accumulate_tool_call(tool_acc, delta_tc)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize any vendor error
            raise LLMError(str(exc)) from exc

        yield Completion(
            content="".join(content_parts),
            tool_calls=tuple(_finalize_tool_calls(tool_acc)),
            finish_reason=finish_reason,
            usage=usage,
        )
