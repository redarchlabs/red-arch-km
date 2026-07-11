"""Unit tests for the LiteLLM provider seam (streaming normalization + catalog).

The real ``litellm`` dependency is stubbed via the ``_litellm`` seam so these
tests exercise the accumulation/normalization logic without any network or heavy
import.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.agents.llm import provider as prov
from api.services.agents.llm.catalog import (
    PROVIDERS,
    VALID_PROVIDERS,
    provider_for_model,
)
from api.services.agents.llm.provider import (
    Completion,
    LLMError,
    LLMProvider,
    TextDelta,
    ToolCallRequest,
    Usage,
)

pytestmark = pytest.mark.unit


# --- catalog ---------------------------------------------------------------


def test_provider_for_model_maps_prefixes():
    assert provider_for_model("anthropic/claude-sonnet-5") == "anthropic"
    assert provider_for_model("gemini/gemini-2.5-pro") == "gemini"
    # Bare OpenAI ids have no prefix -> default provider.
    assert provider_for_model("gpt-5-mini") == "openai"
    # Unknown prefix falls back to openai (LiteLLM default).
    assert provider_for_model("cohere/command-r") == "openai"


def test_catalog_providers_are_valid_and_nonempty():
    assert VALID_PROVIDERS == {"anthropic", "openai", "gemini"}
    for p in PROVIDERS:
        assert p.models, f"{p.name} has no models"
        for m in p.models:
            assert provider_for_model(m.id) == p.name


# --- streaming helpers -----------------------------------------------------


def _tc(index, *, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_accumulate_and_finalize_tool_calls_across_chunks():
    acc: dict = {}
    prov._accumulate_tool_call(acc, _tc(0, id="call_1", name="run_workflow", arguments='{"work'))
    prov._accumulate_tool_call(acc, _tc(0, arguments='flow": "x"}'))
    calls = prov._finalize_tool_calls(acc)
    assert calls == [ToolCallRequest(id="call_1", name="run_workflow", arguments={"workflow": "x"})]


def test_finalize_tolerates_bad_json_and_missing_name():
    acc = {
        0: {"id": "c0", "name": "t", "arguments": "not json"},
        1: {"id": "c1", "name": None, "arguments": "{}"},  # dropped: no name
    }
    calls = prov._finalize_tool_calls(acc)
    assert calls == [ToolCallRequest(id="c0", name="t", arguments={})]


def test_extract_usage():
    chunk = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    assert prov._extract_usage(chunk) == Usage(10, 5, 15)
    assert prov._extract_usage(SimpleNamespace(usage=None)) is None


# --- streaming end-to-end (stubbed litellm) --------------------------------


def _chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, delta=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=usage,
    )


class _FakeLiteLLM:
    def __init__(self, chunks=None, raise_exc=None):
        self._chunks = chunks or []
        self._raise = raise_exc

    async def acompletion(self, **kwargs):
        if self._raise is not None:
            raise self._raise
        chunks = self._chunks

        async def gen():
            for c in chunks:
                yield c

        return gen()


async def _collect(stream):
    return [ev async for ev in stream]


@pytest.mark.asyncio
async def test_stream_yields_text_then_completion_with_tool_call(monkeypatch):
    chunks = [
        _chunk(content="Work"),
        _chunk(content="ing…"),
        _chunk(tool_calls=[_tc(0, id="call_1", name="run_workflow", arguments='{"id":"w1"}')]),
        _chunk(finish_reason="tool_calls", usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)),
    ]
    monkeypatch.setattr(prov, "_litellm", lambda: _FakeLiteLLM(chunks))

    events = await _collect(
        LLMProvider(api_key="k").stream(model="gpt-5-mini", messages=[{"role": "user", "content": "go"}])
    )

    assert events[0] == TextDelta("Work")
    assert events[1] == TextDelta("ing…")
    final = events[-1]
    assert isinstance(final, Completion)
    assert final.content == "Working…"
    assert final.finish_reason == "tool_calls"
    assert final.usage == Usage(7, 3, 10)
    assert final.tool_calls == (ToolCallRequest(id="call_1", name="run_workflow", arguments={"id": "w1"}),)


@pytest.mark.asyncio
async def test_stream_normalizes_provider_errors(monkeypatch):
    monkeypatch.setattr(prov, "_litellm", lambda: _FakeLiteLLM(raise_exc=RuntimeError("boom")))
    with pytest.raises(LLMError, match="boom"):
        await _collect(LLMProvider().stream(model="gpt-5-mini", messages=[]))


@pytest.mark.asyncio
async def test_stream_passes_tools_and_key(monkeypatch):
    captured = {}

    class _Capturing(_FakeLiteLLM):
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return await super().acompletion(**kwargs)

    monkeypatch.setattr(prov, "_litellm", lambda: _Capturing(chunks=[_chunk(content="hi", finish_reason="stop")]))
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    await _collect(
        LLMProvider(api_key="secret").stream(
            model="anthropic/claude-sonnet-5",
            messages=[{"role": "user", "content": "x"}],
            tools=tools,
            temperature=0.2,
        )
    )
    assert captured["api_key"] == "secret"
    assert captured["tools"] == tools
    assert captured["tool_choice"] == "auto"
    assert captured["temperature"] == 0.2
    assert captured["stream"] is True
