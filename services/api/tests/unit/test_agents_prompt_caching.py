"""Unit tests for Anthropic prompt-cache breakpoint insertion.

Caching is applied per-call for Anthropic models only, and only when the tools+system
prefix is large enough to actually cache (else it would add write cost for no benefit).
"""

from __future__ import annotations

import pytest
from api.services.agents.llm.caching import with_cache_breakpoints
from api.services.agents.llm.provider import _extract_usage

pytestmark = pytest.mark.unit

ANTHROPIC = "anthropic/claude-sonnet-5"
BIG = "x" * 20000  # ~5,000-token estimate — over the 4,096 minimum


def _sys(text: str) -> dict:
    return {"role": "system", "content": text}


def test_marks_system_and_last_for_anthropic_large_prefix():
    messages = [_sys(BIG), {"role": "user", "content": "hello there"}]
    out = with_cache_breakpoints(messages, tools=None, model=ANTHROPIC)
    sys_content = out[0]["content"]
    assert isinstance(sys_content, list)
    assert sys_content[-1]["cache_control"] == {"type": "ephemeral"}
    last = out[-1]["content"]
    assert isinstance(last, list)
    assert last[-1]["cache_control"] == {"type": "ephemeral"}
    # Input messages are not mutated (per-call transform only).
    assert messages[0]["content"] == BIG
    assert messages[1]["content"] == "hello there"


def test_small_prefix_unchanged():
    messages = [_sys("short"), {"role": "user", "content": "hi"}]
    out = with_cache_breakpoints(messages, tools=None, model=ANTHROPIC)
    assert out[0]["content"] == "short"
    assert out[-1]["content"] == "hi"


def test_non_anthropic_unchanged():
    messages = [_sys(BIG), {"role": "user", "content": "hi"}]
    for model in ("gpt-5", "gemini/gemini-2.5-flash"):
        out = with_cache_breakpoints(messages, tools=None, model=model)
        assert out[0]["content"] == BIG


def test_tools_count_toward_threshold():
    # Small system but large tool schemas still push the prefix over the minimum.
    tools = [{"schema": "y" * 20000}]
    messages = [_sys("short system"), {"role": "user", "content": "hi"}]
    out = with_cache_breakpoints(messages, tools=tools, model=ANTHROPIC)
    assert isinstance(out[0]["content"], list)


def test_empty_last_message_not_wrapped():
    messages = [_sys(BIG), {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}]
    out = with_cache_breakpoints(messages, tools=None, model=ANTHROPIC)
    assert out[-1]["content"] == ""  # empty content is left alone
    assert isinstance(out[0]["content"], list)  # system still marked


def test_extract_usage_reads_cache_tokens():
    class _Details:
        cached_tokens = 700

    class _Usage:
        prompt_tokens = 1000
        completion_tokens = 200
        total_tokens = 1200
        cache_creation_input_tokens = 300
        prompt_tokens_details = _Details()

    class _Chunk:
        usage = _Usage()

    u = _extract_usage(_Chunk())
    assert u is not None
    assert u.cache_write_tokens == 300
    assert u.cache_read_tokens == 700
    assert u.prompt_tokens == 1000
