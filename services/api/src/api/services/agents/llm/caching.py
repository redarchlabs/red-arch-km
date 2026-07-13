"""Anthropic prompt caching — mark cache breakpoints on the stable prefix.

Agents re-send an identical tools+system prefix on every turn of a run and across
runs; Anthropic prompt caching charges cache hits at 10% of the input price. We add
``cache_control`` breakpoints transparently for **Anthropic models only**, and only
when the cached prefix is large enough to actually cache — below the model minimum
Anthropic won't cache, so marking there would add a 1.25x cache-write cost for no
benefit. In that case we no-op and return the messages unchanged.

Applied per-call inside the provider (not at message construction), so the persisted
resume state in ``AgentRun.input`` stays as plain strings and only ever sees
cache_control at request time.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.llm.catalog import provider_for_model

_EPHEMERAL = {"type": "ephemeral"}
# Rough chars-per-token; used only to decide whether the prefix is worth caching.
_CHARS_PER_TOKEN = 4
# Anthropic's minimum cacheable prefix. Haiku 4.5 / Opus 4.5+ require 4,096 tokens;
# older models 1,024. We use the conservative maximum so we never mark below any min.
_MIN_CACHEABLE_TOKENS = 4096


def _text_len(content: Any) -> int:
    """Approximate character length of a message's content (str or block list)."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(b.get("text", ""))) for b in content if isinstance(b, dict))
    return 0


def _mark(content: Any) -> Any:
    """Return ``content`` as a block list whose last text block carries cache_control.

    A plain string becomes a single ``text`` block; an existing block list gets the
    marker on its last dict block. Empty content is returned unchanged (Anthropic
    rejects empty text blocks).
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": _EPHEMERAL}] if content else content
    if isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else b for b in content]
        for block in reversed(blocks):
            if isinstance(block, dict):
                block["cache_control"] = _EPHEMERAL
                return blocks
    return content


def with_cache_breakpoints(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, model: str
) -> list[dict[str, Any]]:
    """Anthropic-only: mark cache breakpoints on the stable prefix.

    Marks the system message (which caches the preceding **tools + system** prefix)
    and the last message (incremental conversation caching). Returns ``messages``
    unchanged for non-Anthropic models or when the prefix is too small to cache.
    """
    if provider_for_model(model) != "anthropic":
        return messages

    tools_len = len(str(tools)) if tools else 0
    system_len = sum(_text_len(m.get("content")) for m in messages if m.get("role") == "system")
    if (tools_len + system_len) // _CHARS_PER_TOKEN < _MIN_CACHEABLE_TOKENS:
        return messages

    out = [dict(m) for m in messages]
    # Breakpoint 1: the system message → caches the tools + system prefix.
    for msg in out:
        if msg.get("role") == "system":
            msg["content"] = _mark(msg.get("content"))
    # Breakpoint 2: the last message → incremental caching of the growing conversation
    # (skip empty assistant/tool content, which Anthropic rejects).
    last = out[-1] if out else None
    if last is not None and last.get("role") != "system" and _text_len(last.get("content")) > 0:
        last["content"] = _mark(last.get("content"))
    return out
