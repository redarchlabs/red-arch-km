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

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from api.services.agents.llm.caching import with_cache_breakpoints
from api.services.agents.llm.catalog import provider_for_model

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
    # Anthropic prompt-caching accounting: tokens served from cache (billed at 10%)
    # and tokens written to cache (billed at 1.25x). Zero when caching is inactive.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Completion:
    """Terminal event: the assembled assistant message for this turn."""

    content: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    finish_reason: str | None = None
    usage: Usage | None = None
    # Grounding citations (web_research): [{"title","url","snippet"}]. Empty otherwise.
    sources: tuple[dict[str, Any], ...] = ()


LLMStreamEvent = TextDelta | Completion


class LLMError(RuntimeError):
    """A provider call failed (auth, rate limit, unknown model, network)."""


# --- Pure helpers (no litellm needed) --------------------------------------


def _extract_usage(chunk: Any) -> Usage | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    # Cache tokens surface differently across LiteLLM versions/providers: a top-level
    # cache_read_input_tokens (Anthropic) or nested prompt_tokens_details.cached_tokens.
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or getattr(details, "cached_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return Usage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        cache_read_tokens=int(cache_read or 0),
        cache_write_tokens=int(cache_write or 0),
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


def _grounding_sources(response: Any) -> tuple[dict[str, Any], ...]:
    """Best-effort Gemini Google Search grounding citations → [{title,url,snippet}].

    Grounding metadata surfaces in different places across LiteLLM versions (hidden
    params, or on the message), so we probe a few and degrade to no sources rather
    than fail the call.
    """
    meta: Any = None
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        meta = hidden.get("vertex_ai_grounding_metadata") or hidden.get("grounding_metadata")
    if not meta:
        try:
            message = response.choices[0].message
            fields = getattr(message, "provider_specific_fields", None) or {}
            meta = getattr(message, "grounding_metadata", None) or fields.get("grounding_metadata")
        except (AttributeError, IndexError, TypeError):
            meta = None
    if isinstance(meta, list):
        meta = meta[0] if meta else None
    if not isinstance(meta, dict):
        return ()
    chunks = meta.get("groundingChunks") or meta.get("grounding_chunks") or []
    sources: list[dict[str, Any]] = []
    for chunk in chunks:
        web = (chunk or {}).get("web") or {}
        uri = web.get("uri") or web.get("url")
        if uri:
            sources.append({"title": web.get("title") or uri, "url": uri, "snippet": ""})
    return tuple(sources[:5])


def _completion_from_response(response: Any) -> Completion:
    """Build a :class:`Completion` from a non-streaming LiteLLM response."""
    content = ""
    finish_reason: str | None = None
    try:
        choice = response.choices[0]
        content = getattr(choice.message, "content", None) or ""
        finish_reason = getattr(choice, "finish_reason", None)
    except (AttributeError, IndexError, TypeError):
        pass
    return Completion(
        content=content,
        finish_reason=finish_reason,
        usage=_extract_usage(response),
        sources=_grounding_sources(response),
    )


def _litellm() -> Any:
    """Import LiteLLM lazily. Patched in unit tests to avoid the real dependency."""
    import litellm  # noqa: PLC0415 - deliberately lazy

    return litellm


def _anthropic_client(api_key: str | None) -> Any:
    """Lazily build an AsyncAnthropic client — the Batch API is not wrapped by LiteLLM."""
    from anthropic import AsyncAnthropic  # noqa: PLC0415 - deliberately lazy

    return AsyncAnthropic(api_key=api_key)


_BATCH_CUSTOM_ID = "gen"


async def _read_batch_result(client: Any, batch_id: str) -> dict[str, Any]:
    """Read the single result out of a completed Message Batch."""
    results = await client.messages.batches.results(batch_id)
    async for entry in results:
        if getattr(entry, "custom_id", None) != _BATCH_CUSTOM_ID:
            continue
        result = entry.result
        if result.type != "succeeded":
            return {"status": "error", "batch_id": batch_id, "error": f"batch entry {result.type}"}
        message = result.message
        text = "".join(
            getattr(b, "text", "") for b in (message.content or []) if getattr(b, "type", None) == "text"
        )
        usage = getattr(message, "usage", None)
        return {
            "status": "done",
            "batch_id": batch_id,
            "text": text,
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
            },
        }
    return {"status": "error", "batch_id": batch_id, "error": "no batch result found"}


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
        # Anthropic-only, no-op elsewhere or when the prefix is too small to cache.
        messages = with_cache_breakpoints(messages, tools, model)
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

        if usage and (usage.cache_read_tokens or usage.cache_write_tokens):
            logger.debug(
                "prompt cache: read=%d write=%d (model=%s)",
                usage.cache_read_tokens, usage.cache_write_tokens, model,
            )
        yield Completion(
            content="".join(content_parts),
            tool_calls=tuple(_finalize_tool_calls(tool_acc)),
            finish_reason=finish_reason,
            usage=usage,
        )

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Completion:
        """One non-streaming completion → content + usage + grounding sources.

        Used by tools that need a single-shot call (e.g. Google Search grounding),
        which does not stream tool deltas the way :meth:`stream` does.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": with_cache_breakpoints(messages, tools, model),
            "timeout": self.timeout,
            **self.default_params,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if extra:
            kwargs.update(extra)
        try:
            response = await _litellm().acompletion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalize any vendor error
            raise LLMError(str(exc)) from exc
        return _completion_from_response(response)

    async def complete_batch(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        poll_interval: float = 10.0,
        max_wait: float = 180.0,
    ) -> dict[str, Any]:
        """Single-shot generation via Anthropic's Message Batches API (50% off, async).

        Anthropic-only (LiteLLM does not wrap Anthropic batch). Submits a one-request
        batch and polls until it ends or ``max_wait`` elapses. Returns
        ``{"status":"done","text",...}`` or, if still running at the cap,
        ``{"status":"processing","batch_id"}`` (retrieve later via :meth:`retrieve_batch`).
        """
        if provider_for_model(model) != "anthropic":
            raise LLMError(f"batch generation requires an Anthropic model, got '{model}'")
        bare = model.split("/", 1)[1] if "/" in model else model
        params: dict[str, Any] = {"model": bare, "max_tokens": max_tokens, "messages": messages}
        if system:
            params["system"] = system
        client = _anthropic_client(self.api_key)
        try:
            batch = await client.messages.batches.create(
                requests=[{"custom_id": _BATCH_CUSTOM_ID, "params": params}]
            )
            waited = 0.0
            status = batch.processing_status
            while status != "ended" and waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                status = (await client.messages.batches.retrieve(batch.id)).processing_status
            if status != "ended":
                return {"status": "processing", "batch_id": batch.id}
            return await _read_batch_result(client, batch.id)
        except Exception as exc:  # noqa: BLE001 - normalize any vendor error
            raise LLMError(str(exc)) from exc

    async def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        """Fetch a previously-submitted batch: its result if ended, else ``processing``."""
        client = _anthropic_client(self.api_key)
        try:
            got = await client.messages.batches.retrieve(batch_id)
            if got.processing_status != "ended":
                return {"status": "processing", "batch_id": batch_id}
            return await _read_batch_result(client, batch_id)
        except Exception as exc:  # noqa: BLE001 - normalize any vendor error
            raise LLMError(str(exc)) from exc
