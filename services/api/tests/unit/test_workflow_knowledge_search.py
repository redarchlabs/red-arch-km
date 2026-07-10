"""Unit tests for the knowledge_search workflow action + the ``vars.*`` template
namespace (a step's captured output feeding a later step)."""

from __future__ import annotations

import uuid

import pytest
from api.services.workflow.actions import (
    ACTION_REGISTRY,
    ActionContext,
    ActionError,
    _render_deep,
    _render_template,
    _trigger_context,
)


def _ctx(config, *, after=None, vars=None, search=None, retrieve=None):
    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before=None,
        after=after if after is not None else {"text": "who is the president?"},
        vars=vars or {},
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        search_knowledge=search,
        retrieve_knowledge=retrieve,
    )


class TestVarsTemplating:
    def test_render_substitutes_vars(self) -> None:
        out = _render_template("Answer: {{vars.answer}}", {"vars": {"answer": "42"}})
        assert out == "Answer: 42"

    def test_trigger_context_exposes_vars(self) -> None:
        ctx = _ctx({}, vars={"answer": "hello"})
        context = _trigger_context(ctx)
        assert context["vars"] == {"answer": "hello"}

    def test_vars_missing_key_renders_empty(self) -> None:
        assert _render_template("x{{vars.nope}}y", {"vars": {}}) == "xy"

    def test_dotted_path_reaches_into_captured_output(self) -> None:
        # A knowledge_search output captured under 'kb' → {{vars.kb.answer}}.
        ctx = {"vars": {"kb": {"answer": "Jeremy Blair", "sources": []}}}
        assert _render_template("The CEO is {{vars.kb.answer}}.", ctx) == "The CEO is Jeremy Blair."

    def test_render_deep_renders_nested_string_leaves(self) -> None:
        ctx = {"vars": {"kb": {"answer": "42"}}, "after": {"name": "Jo"}}
        body = {"text": "{{vars.kb.answer}}", "meta": {"who": "{{after.name}}"}, "n": 5, "tags": ["{{after.name}}"]}
        assert _render_deep(body, ctx) == {"text": "42", "meta": {"who": "Jo"}, "n": 5, "tags": ["Jo"]}

    def test_render_deep_leaves_non_strings_untouched(self) -> None:
        assert _render_deep({"a": 1, "b": True, "c": None}, {}) == {"a": 1, "b": True, "c": None}


class TestKnowledgeSearchAction:
    @pytest.mark.asyncio
    async def test_searches_with_templated_query_from_after(self) -> None:
        seen: list[dict] = []

        async def _search(opts: dict) -> dict:
            seen.append(opts)
            return {"answer": "The current president.", "sources": [{"document_title": "Civics"}]}

        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": "{{after.text}}"}, search=_search)
        out = await handler.execute(ctx)

        assert seen[0]["query"] == "who is the president?"
        # Graph lookup on by default (back-compat) when the toggle is unset.
        assert seen[0]["use_knowledge_graph"] is True
        assert out["query"] == "who is the president?"
        assert out["answer"] == "The current president."
        assert out["sources"] == [{"document_title": "Civics"}]

    @pytest.mark.asyncio
    async def test_query_via_ref_envelope(self) -> None:
        async def _search(opts: dict) -> dict:
            return {"answer": f"echo:{opts['query']}", "sources": []}

        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": {"$ref": "after.text"}}, after={"text": "hi"}, search=_search)
        out = await handler.execute(ctx)
        assert out["answer"] == "echo:hi"

    @pytest.mark.asyncio
    async def test_use_knowledge_graph_toggle_from_inputs(self) -> None:
        """A per-run ``use_knowledge_graph`` (template string or $ref) reaches the
        search callable — the robot chat's Knowledge-graph switch steering a turn."""
        seen: list[dict] = []

        async def _search(opts: dict) -> dict:
            seen.append(opts)
            return {"answer": "ok", "sources": []}

        handler = ACTION_REGISTRY["knowledge_search"]
        # Template string "false" (as inputs render) must coerce to bool False.
        ctx = _ctx(
            {"query": "hi", "use_knowledge_graph": "{{inputs.use_kg}}"},
            after={"text": "hi"},
            search=_search,
        )
        ctx.inputs = {"use_kg": False}
        await handler.execute(ctx)
        assert seen[0]["use_knowledge_graph"] is False

    @pytest.mark.asyncio
    async def test_synthesize_toggle_from_inputs_ref(self) -> None:
        """``synthesize`` via a $ref envelope preserves the real bool so Fast-mode
        (inputs.synthesize=False) routes to the retrieval-only path."""

        async def _search(opts: dict) -> dict:
            raise AssertionError("Fast mode must not hit the synthesis path")

        async def _retrieve(query: str) -> dict:
            return {"answer": "[1] passages", "sources": [], "passages": []}

        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx(
            {"query": "hi", "synthesize": {"$ref": "inputs.synthesize"}},
            search=_search,
            retrieve=_retrieve,
        )
        ctx.inputs = {"synthesize": False}
        out = await handler.execute(ctx)
        assert out["answer"] == "[1] passages"

    @pytest.mark.asyncio
    async def test_empty_query_raises(self) -> None:
        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": "{{after.missing}}"}, search=lambda q: None)  # renders empty
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_unavailable_search_raises(self) -> None:
        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": "anything"}, search=None)  # not wired in this context
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_answer_flows_to_a_later_step_via_vars(self) -> None:
        """The captured answer is referenceable as {{vars.answer}} — the exact
        wiring the robot's /say step uses."""

        async def _search(opts: dict) -> dict:
            return {"answer": "George", "sources": []}

        search_handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": "{{after.text}}"}, search=_search)
        result = await search_handler.execute(ctx)

        # The engine would capture result["answer"] into run vars under the node's
        # `capture` key; a downstream /say body then templates it.
        run_vars = {"answer": result["answer"]}
        spoken = _render_template("{{vars.answer}}", {"vars": run_vars})
        assert spoken == "George"

    def test_simulate_makes_no_network_call(self) -> None:
        handler = ACTION_REGISTRY["knowledge_search"]

        async def _boom(query: str) -> dict:  # must NOT be called
            raise AssertionError("simulate must not hit the network")

        ctx = _ctx({"query": "{{after.text}}"}, search=_boom)
        out = handler.simulate(ctx)
        assert out["query"] == "who is the president?"
        assert out["answer"] == "<knowledge search result>"
        assert out["sources"] == []

    @pytest.mark.asyncio
    async def test_synthesize_false_uses_retrieval_only(self) -> None:
        """synthesize:false → retrieval-only path (raw passages, no brain-api LLM);
        the passages become ``answer`` for a downstream llm_decide to ground on."""
        searched: list[str] = []

        async def _search(query: str) -> dict:  # must NOT be called in retrieval-only mode
            searched.append(query)
            raise AssertionError("synthesize:false must not call the RAG-synthesis path")

        async def _retrieve(query: str) -> dict:
            return {
                "answer": "[1] Handbook\nThe president is elected every four years.",
                "sources": [{"document_title": "Handbook", "number": 1}],
                "passages": [{"payload": {"text": "elected every four years"}}],
            }

        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": "{{after.text}}", "synthesize": False}, search=_search, retrieve=_retrieve)
        out = await handler.execute(ctx)

        assert searched == []  # synthesis path untouched
        assert out["answer"].startswith("[1] Handbook")
        assert out["sources"] == [{"document_title": "Handbook", "number": 1}]
        assert out["passages"] == [{"payload": {"text": "elected every four years"}}]

    @pytest.mark.asyncio
    async def test_synthesize_false_without_retrieval_raises(self) -> None:
        handler = ACTION_REGISTRY["knowledge_search"]
        ctx = _ctx({"query": "anything", "synthesize": False}, retrieve=None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)


class _FakeChat:
    def __init__(self, reply, sink):
        self._reply = reply
        self._sink = sink

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
    def __init__(self, reply="The CEO is Jeremy Blair.", sink=None):
        self.chat = type("chat", (), {"completions": _FakeChat(reply, sink if sink is not None else [])})()


class TestSummarizeForSpeech:
    @pytest.mark.asyncio
    async def test_builds_spoken_prompt_and_returns_reply(self) -> None:
        from api.services.spoken_summary import summarize_for_speech

        calls: list[dict] = []
        client = _FakeClient(reply="The CEO is Jeremy Blair.", sink=calls)
        out = await summarize_for_speech(
            client,
            "gpt-5-nano",
            text="The CEO is Jeremy Blair [1]. Founded 2020.",
            question="who is the CEO?",
            max_words=15,
        )
        assert out == "The CEO is Jeremy Blair."
        sys_msg = calls[0]["messages"][0]["content"]
        assert "at most 15 words" in sys_msg and "citation" in sys_msg.lower()
        assert "who is the CEO?" in calls[0]["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_falls_back_to_input_when_model_returns_empty(self) -> None:
        from api.services.spoken_summary import summarize_for_speech

        client = _FakeClient(reply="", sink=[])
        out = await summarize_for_speech(client, "gpt-5-nano", text="fallback text")
        assert out == "fallback text"


def _summ_ctx(config, *, after=None, vars=None, summarize=None):
    return ActionContext(
        org_id=uuid.uuid4(),
        record_id=None,
        before=None,
        after=after if after is not None else {"text": "who is the CEO?"},
        vars=vars or {},
        config=config,
        trigger_repo=None,  # type: ignore[arg-type]
        repo_for_slug=None,  # type: ignore[arg-type]
        summarize=summarize,
    )


class TestSummarizeAction:
    @pytest.mark.asyncio
    async def test_condenses_captured_answer_with_question_context(self) -> None:
        seen: list[dict] = []

        async def _summarize(opts: dict) -> str:
            seen.append(opts)
            return "The CEO is Jeremy Blair."

        handler = ACTION_REGISTRY["summarize"]
        ctx = _summ_ctx(
            {"text": "{{vars.kb.answer}}", "question": "{{after.text}}", "max_words": 20},
            vars={"kb": {"answer": "The CEO is Jeremy Blair [1]. He founded the company in..."}},
        )
        ctx.summarize = _summarize  # type: ignore[assignment]
        out = await handler.execute(ctx)

        assert seen[0]["text"].startswith("The CEO is Jeremy Blair [1]")
        assert seen[0]["question"] == "who is the CEO?"
        assert seen[0]["max_words"] == 20
        assert out["text"] == "The CEO is Jeremy Blair."
        assert out["input_chars"] > out["output_chars"]

    @pytest.mark.asyncio
    async def test_max_words_and_model_resolve_from_inputs(self) -> None:
        """The Concise / Answer-model toggles reach the summarizer: max_words via a
        ``{{ inputs.max_words }}`` numeric-string template, model via a $ref envelope."""
        seen: list[dict] = []

        async def _summarize(opts: dict) -> str:
            seen.append(opts)
            return "short line"

        handler = ACTION_REGISTRY["summarize"]
        ctx = _summ_ctx(
            {
                "text": "a long grounded answer",
                "max_words": "{{inputs.max_words}}",
                "model": {"$ref": "inputs.answer_model"},
            },
        )
        ctx.inputs = {"max_words": 18, "answer_model": "gpt-5-nano"}
        ctx.summarize = _summarize  # type: ignore[assignment]
        await handler.execute(ctx)
        assert seen[0]["max_words"] == 18
        assert seen[0]["model"] == "gpt-5-nano"

    @pytest.mark.asyncio
    async def test_empty_text_raises(self) -> None:
        handler = ACTION_REGISTRY["summarize"]
        ctx = _summ_ctx({"text": "{{vars.kb.missing}}"}, vars={"kb": {}}, summarize=lambda o: None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    @pytest.mark.asyncio
    async def test_unavailable_summarize_raises(self) -> None:
        handler = ACTION_REGISTRY["summarize"]
        ctx = _summ_ctx({"text": "some text"}, summarize=None)
        with pytest.raises(ActionError):
            await handler.execute(ctx)

    def test_simulate_makes_no_llm_call(self) -> None:
        handler = ACTION_REGISTRY["summarize"]

        async def _boom(opts: dict) -> str:
            raise AssertionError("simulate must not call the LLM")

        ctx = _summ_ctx({"text": "{{vars.kb.answer}}"}, vars={"kb": {"answer": "long text"}}, summarize=_boom)
        out = handler.simulate(ctx)
        assert out["text"] == "<summarized spoken reply>"
        assert out["input_chars"] == len("long text")
