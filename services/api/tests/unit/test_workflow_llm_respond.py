"""Unit tests for the llm_respond workflow action — the role-play persona + coach node
that powers a training simulator.

Proves the design's headline claims: string config is templated from the run context, the
structured ``{reply, coach, done}`` turn (persona voice vs. separate coach voice) is what a
downstream /say + gateway consume, the helper is strict-JSON-schema constrained, and a
malformed model response degrades gracefully (empty strings + done=false) rather than crashing.
"""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError


def _ctx(config, *, after=None, vars=None, inputs=None, respond=None):
    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before=None,
        after=after if after is not None else {"text": "I want a refund now!"},
        vars=vars or {},
        inputs=inputs or {},
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        respond=respond,
    )


class TestLlmRespondAction:
    def test_registered(self) -> None:
        assert "llm_respond" in ACTION_REGISTRY
        assert ACTION_REGISTRY["llm_respond"].type == "llm_respond"

    @pytest.mark.asyncio
    async def test_templates_config_and_returns_structured_turn(self) -> None:
        seen: list[dict] = []

        async def _respond(opts: dict) -> dict:
            seen.append(opts)
            return {"reply": "I understand you're upset.", "coach": "Acknowledge the emotion first.", "done": False}

        handler = ACTION_REGISTRY["llm_respond"]
        ctx = _ctx(
            {
                "persona": "An angry customer named {{after.name}} demanding a refund.",
                "scenario": "Front desk complaint.",
                "objective": "De-escalate calmly.",
                "grounding": "{{vars.sop.answer}}",
                "user_message": "{{after.text}}",
            },
            after={"text": "I want a refund now!", "name": "Pat"},
            vars={"sop": {"answer": "Refunds are allowed within 30 days with a receipt."}},
            respond=_respond,
        )
        out = await handler.execute(ctx)

        # String config templated from after/vars and threaded to the collaborator.
        assert seen[0]["persona"] == "An angry customer named Pat demanding a refund."
        assert seen[0]["scenario"] == "Front desk complaint."
        assert seen[0]["objective"] == "De-escalate calmly."
        assert seen[0]["grounding"] == "Refunds are allowed within 30 days with a receipt."
        assert seen[0]["user_message"] == "I want a refund now!"
        # The structured turn flows straight through (persona reply + separate coach voice).
        assert out == {"reply": "I understand you're upset.", "coach": "Acknowledge the emotion first.", "done": False}

    @pytest.mark.asyncio
    async def test_history_via_ref_envelope_resolves_to_list(self) -> None:
        """``history`` may be a $ref to a captured conversation list — it is passed through."""
        seen: list[dict] = []

        async def _respond(opts: dict) -> dict:
            seen.append(opts)
            return {"reply": "ok", "coach": "tip", "done": True}

        handler = ACTION_REGISTRY["llm_respond"]
        convo = [{"role": "learner", "content": "Hi"}, {"role": "persona", "content": "Hmph."}]
        ctx = _ctx(
            {"persona": "grumpy", "user_message": "sorry", "history": {"$ref": "vars.convo"}},
            vars={"convo": convo},
            respond=_respond,
        )
        out = await handler.execute(ctx)
        assert seen[0]["history"] == convo
        assert out["done"] is True

    @pytest.mark.asyncio
    async def test_coerces_missing_fields_defensively(self) -> None:
        """A collaborator returning a partial/odd dict is shaped into the strict turn."""

        async def _respond(opts: dict) -> dict:
            return {"reply": None}  # coach/done missing, reply not a string

        handler = ACTION_REGISTRY["llm_respond"]
        ctx = _ctx({"persona": "p", "user_message": "hi"}, respond=_respond)
        out = await handler.execute(ctx)
        assert out == {"reply": "", "coach": "", "done": False}

    @pytest.mark.asyncio
    async def test_empty_persona_raises(self) -> None:
        handler = ACTION_REGISTRY["llm_respond"]
        ctx = _ctx({"persona": "{{after.missing}}", "user_message": "hi"}, respond=lambda o: None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_empty_user_message_raises(self) -> None:
        handler = ACTION_REGISTRY["llm_respond"]
        ctx = _ctx({"persona": "grumpy", "user_message": "{{after.missing}}"}, respond=lambda o: None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_unavailable_respond_raises(self) -> None:
        handler = ACTION_REGISTRY["llm_respond"]
        ctx = _ctx({"persona": "grumpy", "user_message": "hi"}, respond=None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    def test_simulate_makes_no_llm_call(self) -> None:
        handler = ACTION_REGISTRY["llm_respond"]

        async def _boom(opts: dict) -> dict:
            raise AssertionError("simulate must not call the LLM")

        ctx = _ctx({"persona": "grumpy", "user_message": "{{after.text}}"}, respond=_boom)
        out = handler.simulate(ctx)
        assert out == {"reply": "", "coach": "", "done": False}


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


class TestRespondActionHelper:
    @pytest.mark.asyncio
    async def test_builds_constrained_schema_and_parses_json(self) -> None:
        import json

        from api.services.llm_respond import respond_action

        reply = json.dumps({"reply": "I hear you.", "coach": "Stay calm and paraphrase.", "done": False})
        calls: list[dict] = []
        client = _FakeClient(reply, calls)
        out = await respond_action(
            client,
            "gpt-4.1-mini",
            persona="angry customer",
            user_message="refund now",
            scenario="front desk",
            objective="de-escalate",
            grounding="Refunds within 30 days.",
            system="Play the persona.",
        )
        assert out == {"reply": "I hear you.", "coach": "Stay calm and paraphrase.", "done": False}
        # The request is a strict json_schema locked to reply/coach/done.
        fmt = calls[0]["response_format"]
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
        props = fmt["json_schema"]["schema"]["properties"]
        assert set(props) == {"reply", "coach", "done"}
        assert calls[0]["messages"][0]["content"] == "Play the persona."  # system override
        # Grounding is threaded into the user prompt (factual accuracy).
        assert "Refunds within 30 days." in calls[0]["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_malformed_json_falls_back_gracefully(self) -> None:
        from api.services.llm_respond import respond_action

        client = _FakeClient("not json at all", [])
        out = await respond_action(client, "m", persona="p", user_message="u")
        assert out == {"reply": "", "coach": "", "done": False}
