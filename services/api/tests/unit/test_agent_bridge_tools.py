"""Unit tests for the workflow-bridge tools (complete_task / escalate_task), the
output-schema validator, and RunFinished termination semantics in the loop."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from api.models.agent import Agent
from api.services.agents.llm.provider import Completion, TextDelta, ToolCallRequest
from api.services.agents.runtime import RunFinished, run_agent_loop
from api.services.agents.tools.bridge import validate_output, workflow_bridge_specs
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

pytestmark = pytest.mark.unit

SCHEMA = {
    "category": {"type": "string", "enum": ["billing", "tech", "other"]},
    "priority": {"type": "string", "required": False},
    "summary": {"type": "string", "maxLength": 20},
    "score": {"type": "number", "minimum": 0, "maximum": 10},
}


class TestValidateOutput:
    def test_valid_payload_passes(self):
        assert validate_output(SCHEMA, {"category": "tech", "summary": "short", "score": 5}) == []

    def test_missing_required_field(self):
        errors = validate_output(SCHEMA, {"summary": "s", "score": 1})
        assert any("category: required" in e for e in errors)

    def test_optional_field_may_be_absent(self):
        assert validate_output(SCHEMA, {"category": "other", "summary": "s", "score": 0}) == []

    def test_enum_violation(self):
        errors = validate_output(SCHEMA, {"category": "nope", "summary": "s", "score": 1})
        assert any("must be one of" in e for e in errors)

    def test_type_violation(self):
        errors = validate_output(SCHEMA, {"category": "tech", "summary": 42, "score": 1})
        assert any("summary: expected string" in e for e in errors)

    def test_max_length_and_range(self):
        errors = validate_output(SCHEMA, {"category": "tech", "summary": "x" * 21, "score": 11})
        assert any("maxLength" in e for e in errors)
        assert any("above maximum" in e for e in errors)

    def test_unknown_field_rejected(self):
        errors = validate_output(SCHEMA, {"category": "tech", "summary": "s", "score": 1, "extra": True})
        assert any("extra: not in the output schema" in e for e in errors)

    def test_boolean_is_not_a_number(self):
        errors = validate_output({"n": {"type": "number"}}, {"n": True})
        assert any("n: expected number" in e for e in errors)


def _ctx() -> ToolContext:
    agent = Agent(name="a", provider="openai", model="m", kind="operator", grants={})
    return ToolContext(session=None, org_id=uuid4(), settings=None, agent=agent)


class TestBridgeSpecs:
    @pytest.mark.asyncio
    async def test_complete_task_rejects_bad_output_with_errors(self):
        complete = next(s for s in workflow_bridge_specs(SCHEMA) if s.name == "complete_task")
        out = await complete.handler(_ctx(), {"category": "nope"})
        assert out["error"] and out["validation_errors"]

    @pytest.mark.asyncio
    async def test_complete_task_raises_run_finished_with_output(self):
        complete = next(s for s in workflow_bridge_specs(SCHEMA) if s.name == "complete_task")
        with pytest.raises(RunFinished) as exc:
            await complete.handler(_ctx(), {"category": "tech", "summary": "s", "score": 3})
        assert exc.value.status == "done"
        assert exc.value.payload["output"]["category"] == "tech"

    @pytest.mark.asyncio
    async def test_escalate_task_raises_escalated_with_reason(self):
        escalate = next(s for s in workflow_bridge_specs({}) if s.name == "escalate_task")
        with pytest.raises(RunFinished) as exc:
            await escalate.handler(_ctx(), {"reason": "ambiguous request"})
        assert exc.value.status == "escalated"
        assert exc.value.payload["reason"] == "ambiguous request"

    def test_specs_are_terminal_and_always_allowed(self):
        for spec in workflow_bridge_specs(SCHEMA):
            assert spec.terminal and spec.always_allowed and not spec.side_effecting

    def test_llm_visible_schema_carries_constraints(self):
        complete = next(s for s in workflow_bridge_specs(SCHEMA) if s.name == "complete_task")
        params = complete.parameters
        assert params["properties"]["category"]["enum"] == ["billing", "tech", "other"]
        assert "priority" not in params["required"]
        assert "category" in params["required"]


class _FakeProvider:
    def __init__(self, turns):
        self._turns = list(turns)

    async def stream(self, **_kwargs) -> AsyncIterator:
        deltas, completion = self._turns.pop(0)
        for d in deltas:
            yield TextDelta(d)
        yield completion


async def _emit(_ev):
    pass


@pytest.mark.asyncio
async def test_terminal_tool_runs_last_in_batch_then_ends_run():
    """complete_task batched with a write: the write lands FIRST, then the run
    ends — a batch's side effects never execute after completion."""
    order: list[str] = []

    async def write_handler(ctx, args):
        order.append("write")
        return {"ok": True}

    write = ToolSpec(
        name="write_note",
        description="w",
        parameters={"type": "object", "properties": {}},
        category=Category.READ,
        handler=write_handler,
        always_allowed=True,
    )
    specs = [*workflow_bridge_specs({"category": {"type": "string"}}), write]
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(
                        # Model emits complete FIRST — ordering must still run it last.
                        ToolCallRequest(id="c1", name="complete_task", arguments={"category": "x"}),
                        ToolCallRequest(id="c2", name="write_note", arguments={}),
                    ),
                    finish_reason="tool_calls",
                ),
            )
        ]
    )
    agent = Agent(name="a", provider="openai", model="m", kind="operator", grants={})
    with pytest.raises(RunFinished) as exc:
        await run_agent_loop(
            provider=provider,
            agent=agent,
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=specs,
            ctx=_ctx(),
            emit=_emit,
            max_iterations=4,
        )
    assert order == ["write"]
    assert exc.value.status == "done"


@pytest.mark.asyncio
async def test_run_finished_carries_accumulated_usage():
    from api.services.agents.llm.provider import Usage

    specs = workflow_bridge_specs({"category": {"type": "string"}})
    provider = _FakeProvider(
        [
            (
                [],
                Completion(
                    content="",
                    tool_calls=(ToolCallRequest(id="c1", name="complete_task", arguments={"category": "x"}),),
                    finish_reason="tool_calls",
                    usage=Usage(10, 5, 15),
                ),
            )
        ]
    )
    agent = Agent(name="a", provider="openai", model="m", kind="operator", grants={})
    with pytest.raises(RunFinished) as exc:
        await run_agent_loop(
            provider=provider,
            agent=agent,
            model="m",
            messages=[{"role": "user", "content": "go"}],
            specs=specs,
            ctx=_ctx(),
            emit=_emit,
            max_iterations=4,
        )
    assert exc.value.total_tokens == 15


@pytest.mark.asyncio
async def test_prose_answer_ends_loop_normally_without_run_finished():
    """The executor maps this to 'escalated' for workflow runs; the loop itself
    just returns — no RunFinished, no crash."""
    specs = workflow_bridge_specs({"category": {"type": "string"}})
    provider = _FakeProvider([(["done!"], Completion(content="done!", finish_reason="stop"))])
    agent = Agent(name="a", provider="openai", model="m", kind="operator", grants={})
    result = await run_agent_loop(
        provider=provider,
        agent=agent,
        model="m",
        messages=[{"role": "user", "content": "go"}],
        specs=specs,
        ctx=_ctx(),
        emit=_emit,
        max_iterations=4,
    )
    assert result.final_content == "done!"
