"""Unit tests for the batch generation tools (Anthropic Message Batches, 50% off)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from api.models.agent import Agent
from api.services.agents.authority import Decision, decide
from api.services.agents.llm.provider import LLMError, LLMProvider
from api.services.agents.tools.batch_generate import (
    BATCH_GENERATE,
    CHECK_BATCH,
    _batch_generate,
    _check_batch,
)
from api.services.agents.tools.registry import base_tool_specs
from api.services.agents.tools.spec import Category, ToolContext

pytestmark = pytest.mark.unit

BG = "api.services.agents.tools.batch_generate"
ANTHROPIC = "anthropic/claude-haiku-4-5-20251001"


def _agent(kind: str, model: str = ANTHROPIC, **grants) -> Agent:
    return Agent(name="a", provider="anthropic", model=model, kind=kind, grants=grants)


def _ctx(model: str = ANTHROPIC) -> ToolContext:
    return ToolContext(
        session=None,
        org_id=uuid.uuid4(),
        settings=SimpleNamespace(agent_batch_poll_interval_seconds=1, agent_batch_max_wait_seconds=5),
        agent=_agent("operator", model=model, tools=["batch_generate", "check_batch"]),
    )


async def test_batch_generate_done():
    done = {"status": "done", "text": "a punchy blurb", "batch_id": "b1"}
    with (
        patch(f"{BG}.resolve_provider_key", AsyncMock(return_value="ak")),
        patch.object(LLMProvider, "complete_batch", AsyncMock(return_value=done)) as m,
    ):
        out = await _batch_generate(_ctx(), {"prompt": "draft a blurb"})
    assert out["status"] == "done" and out["text"] == "a punchy blurb"
    _, kwargs = m.call_args
    assert kwargs["model"] == ANTHROPIC
    assert kwargs["messages"][0]["content"] == "draft a blurb"


async def test_batch_generate_processing_passthrough():
    with (
        patch(f"{BG}.resolve_provider_key", AsyncMock(return_value="ak")),
        patch.object(LLMProvider, "complete_batch", AsyncMock(return_value={"status": "processing", "batch_id": "b2"})),
    ):
        out = await _batch_generate(_ctx(), {"prompt": "x"})
    assert out == {"status": "processing", "batch_id": "b2"}


async def test_requires_prompt():
    out = await _batch_generate(_ctx(), {"prompt": "  "})
    assert out["error"] == "prompt is required"


async def test_non_anthropic_model_rejected():
    # Model check happens before key resolution, so no key mock needed.
    out = await _batch_generate(_ctx(model="gpt-5"), {"prompt": "x"})
    assert "Anthropic model" in out["error"]


async def test_missing_key_errors():
    with patch(f"{BG}.resolve_provider_key", AsyncMock(return_value=None)):
        out = await _batch_generate(_ctx(), {"prompt": "x"})
    assert "Anthropic API key" in out["error"]


async def test_error_surfaced():
    with (
        patch(f"{BG}.resolve_provider_key", AsyncMock(return_value="ak")),
        patch.object(LLMProvider, "complete_batch", AsyncMock(side_effect=LLMError("boom"))),
    ):
        out = await _batch_generate(_ctx(), {"prompt": "x"})
    assert "batch generation failed" in out["error"]


async def test_check_batch_done():
    with (
        patch(f"{BG}.resolve_provider_key", AsyncMock(return_value="ak")),
        patch.object(
            LLMProvider, "retrieve_batch",
            AsyncMock(return_value={"status": "done", "text": "t", "batch_id": "b1"}),
        ),
    ):
        out = await _check_batch(_ctx(), {"batch_id": "b1"})
    assert out["status"] == "done"


async def test_check_batch_requires_id():
    out = await _check_batch(_ctx(), {})
    assert out["error"] == "batch_id is required"


def test_registered():
    names = {s.name for s in base_tool_specs()}
    assert {"batch_generate", "check_batch"} <= names


def test_authority_internal_execute_operator_only():
    for spec in (BATCH_GENERATE, CHECK_BATCH):
        assert spec.category == Category.EXECUTE
        assert spec.side_effecting is False  # internal generation → runs free
        granted = _agent("operator", tools=[spec.name])
        assert decide(granted, spec, autonomy="high_touch").decision is Decision.ALLOW
        assert decide(_agent("operator"), spec).decision is Decision.DENY
        for kind in ("advisory", "coordinator"):
            assert decide(_agent(kind, tools=[spec.name]), spec).decision is Decision.DENY
