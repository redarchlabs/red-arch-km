"""Unit tests for the llm_decide workflow action — the constrained-LLM 'steering' node.

Proves the design's headline claim: the LLM's choice is locked to the robot's advertised
vocabulary (gesture/mood are enum-constrained + enforced), rules of engagement flow through
as the system prompt, and the structured decision is what a downstream /say + gateway consume.
"""

from __future__ import annotations

import json
import uuid

import pytest
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError


def _ctx(config, *, after=None, vars=None, decide=None):
    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before=None,
        after=after if after is not None else {"text": "how big is the sun?"},
        vars=vars or {},
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        decide=decide,
    )


GESTURES = ["nod", "greet", "celebrate", "think"]
MOODS = ["calm", "happy", "curious", "excited"]


class TestLlmDecideAction:
    @pytest.mark.asyncio
    async def test_templates_inputs_and_returns_structured_decision(self) -> None:
        seen: list[dict] = []

        async def _decide(opts: dict) -> dict:
            seen.append(opts)
            return {"say": "It's huge!", "gesture": "celebrate", "mood": "excited", "done": False, "reason": "engage"}

        handler = ACTION_REGISTRY["llm_decide"]
        ctx = _ctx(
            {
                "question": "{{after.text}}",
                "context": "{{vars.kb.answer}}",
                "system": "Be kind and stay on space.",
                "gestures": GESTURES,
                "moods": MOODS,
            },
            vars={"kb": {"answer": "The Sun is about 1.4 million km across."}},
            decide=_decide,
        )
        out = await handler.execute(ctx)

        # Inputs were templated from after/vars and the rules-of-engagement passed through.
        assert seen[0]["question"] == "how big is the sun?"
        assert seen[0]["context"] == "The Sun is about 1.4 million km across."
        assert seen[0]["system"] == "Be kind and stay on space."
        assert seen[0]["gestures"] == GESTURES
        # The structured decision flows straight through (a later /say uses vars.decision.say).
        assert out == {"say": "It's huge!", "gesture": "celebrate", "mood": "excited", "done": False, "reason": "engage"}

    @pytest.mark.asyncio
    async def test_enforces_the_vocabulary(self) -> None:
        """Even if a model returns an out-of-vocab move, the node nulls it so a /gesture
        or /mood call is never handed something the robot rejects."""

        async def _decide(opts: dict) -> dict:
            return {"say": "hi", "gesture": "backflip", "mood": "smug", "done": True, "reason": "x"}

        handler = ACTION_REGISTRY["llm_decide"]
        ctx = _ctx({"question": "hi", "gestures": GESTURES, "moods": MOODS}, decide=_decide)
        out = await handler.execute(ctx)
        assert out["gesture"] is None  # "backflip" not in GESTURES
        assert out["mood"] is None  # "smug" not in MOODS
        assert out["done"] is True

    @pytest.mark.asyncio
    async def test_empty_question_raises(self) -> None:
        handler = ACTION_REGISTRY["llm_decide"]
        ctx = _ctx({"question": "{{after.missing}}"}, decide=lambda o: None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_unavailable_decide_raises(self) -> None:
        handler = ACTION_REGISTRY["llm_decide"]
        ctx = _ctx({"question": "anything"}, decide=None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    def test_simulate_makes_no_llm_call(self) -> None:
        handler = ACTION_REGISTRY["llm_decide"]

        async def _boom(opts: dict) -> dict:
            raise AssertionError("simulate must not call the LLM")

        ctx = _ctx({"question": "{{after.text}}"}, decide=_boom)
        out = handler.simulate(ctx)
        assert out["done"] is True and out["reason"] == "dry-run"


class _FakeChat:
    def __init__(self, reply, sink):
        self._reply, self._sink = reply, sink

    async def create(self, **kwargs):
        self._sink.append(kwargs)

        class _M:
            content = self._reply

        class _C:
            message = _M()

        class _R:
            choices = [_C()]

        return _R()


class _FakeClient:
    def __init__(self, reply, sink):
        self.chat = type("chat", (), {"completions": _FakeChat(reply, sink)})()


class TestDecideAction:
    @pytest.mark.asyncio
    async def test_builds_constrained_schema_and_parses_json(self) -> None:
        from api.services.llm_decide import decide_action

        reply = json.dumps({"say": "The Sun is a star.", "gesture": "nod", "mood": "curious", "done": False, "reason": "ok"})
        calls: list[dict] = []
        client = _FakeClient(reply, calls)
        out = await decide_action(
            client,
            "gpt-4.1-mini",
            question="what is the sun?",
            context="The Sun is a star.",
            gestures=GESTURES,
            moods=MOODS,
            system="Stay on space.",
        )
        assert out["say"] == "The Sun is a star." and out["gesture"] == "nod"
        # The request is a strict json_schema with gesture/mood enum-locked to the vocabulary.
        fmt = calls[0]["response_format"]
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
        props = fmt["json_schema"]["schema"]["properties"]
        assert None in props["gesture"]["enum"] and set(GESTURES).issubset(props["gesture"]["enum"])
        assert calls[0]["messages"][0]["content"] == "Stay on space."  # rules of engagement
