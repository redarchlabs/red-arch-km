"""``grep_documents`` and ``read_document`` — what they hand brain-api, and what
they hand the model.

Both resolve their reach through ``knowledge.resolve_knowledge_target``, the
function ``search_knowledge`` uses, so the scoping rules themselves are pinned in
``test_agent_knowledge_tool.py``. These pin that the new tools go through it —
an actor's masks arrive at brain-api, an unattended run fails closed, an explicit
empty scope never becomes "no filter" — and that results and failures come back
in a shape a model can act on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.services.agents.tools import document_text
from api.services.agents.tools.registry import base_tool_specs

pytestmark = pytest.mark.unit


@dataclass
class _FakeAgent:
    name: str = "analyst"
    grants: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeCtx:
    agent: _FakeAgent
    session: Any = None
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    settings: Any = "configured"
    actor_user_id: uuid.UUID | None = None
    via_api_key: bool = False
    api_key_id: uuid.UUID | None = None


_GREP_RESULT = {
    "documents_matched": 1,
    "truncated": False,
    "documents": [
        {
            "document_key": "pumps",
            "title": "Pumps",
            "occurrences": 2,
            "matching_chunks": 1,
            "snippets": [{"chunk": 4, "section": None, "text": "asset tag PMP-2041-X"}],
        }
    ],
}

_READ_RESULT = {
    "document_key": "pumps",
    "title": "Pumps",
    "total_chunks": 9,
    "start_chunk": 3,
    "end_chunk": 7,
    "next_chunk": 7,
    "text": "…the six pumps…",
}


@pytest.fixture
def brain(monkeypatch):
    """A fake brain-api client recording every call; ``brain["fail"]`` makes it raise."""
    state: dict[str, Any] = {"calls": [], "read": _READ_RESULT, "grep": _GREP_RESULT}

    class _Client:
        def __init__(self, _settings): ...

        async def text_search(self, **kwargs):
            state["calls"].append(("text_search", kwargs))
            if state.get("fail"):
                raise RuntimeError("http://brain-api:8001 refused")
            return state["grep"]

        async def read_document(self, **kwargs):
            state["calls"].append(("read_document", kwargs))
            if state.get("fail"):
                raise RuntimeError("http://brain-api:8001 refused")
            return state["read"]

    async def _model(_session, _org):
        return None

    async def _keys(_session, _org, _profile_id):
        return state.get("resolved", [0, 99])

    async def _org_name(_ctx):
        return "Acme"

    monkeypatch.setattr("api.services.brain_client.BrainAPIClient", _Client)
    monkeypatch.setattr("api.services.org_llm.org_default_llm_model", _model)
    monkeypatch.setattr("api.services.search_access.resolve_profile_access_keys", _keys)
    monkeypatch.setattr("api.services.agents.tools.knowledge._own_org_name", _org_name)
    return state


def _actor_ctx(**kwargs: Any) -> _FakeCtx:
    return _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4(), **kwargs)


GREP = document_text.GREP_DOCUMENTS.handler
READ = document_text.READ_DOCUMENT.handler


class TestGrepDocuments:
    async def test_reads_with_the_actors_masks(self, brain) -> None:
        ctx = _actor_ctx()

        out = await GREP(ctx, {"query": "PMP-2041-X", "mode": "phrase"})

        _, kwargs = brain["calls"][0]
        assert kwargs["access_keys"] == [0, 99]
        assert kwargs["folder_tags"] is None
        assert kwargs["tenant_id"] == str(ctx.org_id)
        assert kwargs["mode"] == "phrase"
        assert out["searched_org"] == "Acme"
        assert out["documents"][0]["document_key"] == "pumps"

    async def test_an_unattended_run_fails_closed(self, brain) -> None:
        out = await GREP(_FakeCtx(agent=_FakeAgent()), {"query": "x"})
        assert "no user to read on behalf of" in out["error"]
        assert brain["calls"] == []

    async def test_an_actor_with_no_access_never_reaches_brain(self, brain) -> None:
        brain["resolved"] = []
        out = await GREP(_actor_ctx(), {"query": "x"})
        assert "no knowledge-base access" in out["error"]
        assert brain["calls"] == []

    async def test_org_grant_reads_unrestricted_for_an_unattended_run(self, brain) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(grants={"knowledge_scope": "org"}))
        await GREP(ctx, {"query": "x"})
        assert brain["calls"][0][1]["access_keys"] is None

    async def test_no_match_explains_what_to_try(self, brain) -> None:
        brain["grep"] = {"documents_matched": 0, "truncated": False, "documents": []}
        out = await GREP(_actor_ctx(), {"query": "turbines"})
        assert out["documents"] == []
        assert "whole word" in out["note"]

    async def test_truncation_is_surfaced(self, brain) -> None:
        brain["grep"] = {**_GREP_RESULT, "truncated": True}
        out = await GREP(_actor_ctx(), {"query": "pump"})
        assert "narrow" in out["note"]

    @pytest.mark.parametrize(
        ("args", "fragment"), [({"query": " "}, "required"), ({"query": "x", "mode": "regex"}, "mode")]
    )
    async def test_bad_arguments_are_refused_before_any_lookup(self, brain, args, fragment) -> None:
        out = await GREP(_actor_ctx(), args)
        assert fragment in out["error"]
        assert brain["calls"] == []

    async def test_an_outage_is_reported_without_internal_detail(self, brain) -> None:
        brain["fail"] = True
        out = await GREP(_actor_ctx(), {"query": "x"})
        assert "temporarily unavailable" in out["error"]
        assert "brain-api:8001" not in out["error"]


class TestReadDocument:
    async def test_reads_with_the_actors_masks_and_reports_paging(self, brain) -> None:
        out = await READ(_actor_ctx(), {"document_key": "pumps", "start_chunk": 3})

        _, kwargs = brain["calls"][0]
        assert kwargs["access_keys"] == [0, 99]
        assert kwargs["document_key"] == "pumps" and kwargs["start_chunk"] == 3
        assert out["next_chunk"] == 7
        assert out["chunks"] == "3-6 of 9"
        assert out["text"] == "…the six pumps…"

    async def test_missing_or_unreadable_is_one_answer(self, brain) -> None:
        brain["read"] = None
        out = await READ(_actor_ctx(), {"document_key": "hr-salaries"})
        assert "No readable document" in out["error"]

    async def test_an_unattended_run_fails_closed(self, brain) -> None:
        out = await READ(_FakeCtx(agent=_FakeAgent()), {"document_key": "pumps"})
        assert "no user to read on behalf of" in out["error"]
        assert brain["calls"] == []

    @pytest.mark.parametrize(
        ("args", "fragment"),
        [({}, "document_key is required"), ({"document_key": "d", "start_chunk": "soon"}, "whole number")],
    )
    async def test_bad_arguments_are_refused_before_any_lookup(self, brain, args, fragment) -> None:
        out = await READ(_actor_ctx(), args)
        assert fragment in out["error"]
        assert brain["calls"] == []

    async def test_a_negative_start_reads_from_the_beginning(self, brain) -> None:
        await READ(_actor_ctx(), {"document_key": "pumps", "start_chunk": -4})
        assert brain["calls"][0][1]["start_chunk"] == 0

    async def test_an_outage_is_reported_without_internal_detail(self, brain) -> None:
        brain["fail"] = True
        out = await READ(_actor_ctx(), {"document_key": "pumps"})
        assert "temporarily unavailable" in out["error"]
        assert "brain-api:8001" not in out["error"]


def test_both_tools_are_offered_to_every_agent_as_reads() -> None:
    specs = {s.name: s for s in base_tool_specs()}
    for name in ("grep_documents", "read_document"):
        assert specs[name].always_allowed
        assert specs[name].category == "read"
        assert not specs[name].side_effecting


class TestScopedKeyRuns:
    """A run started by a scoped API key reads with the key's masks and folders."""

    @pytest.fixture
    def scoped_key(self, monkeypatch):
        from api.services.agents.tools.key_scope import RunKeyScope

        folder = uuid.uuid4()
        state: dict[str, Any] = {
            "scope": RunKeyScope(scoped=True, scopes=frozenset(), masks=(0, 42), folder_ids=frozenset({folder}))
        }

        async def _run_key_scope(_ctx):
            return state["scope"]

        monkeypatch.setattr("api.services.agents.tools.knowledge.run_key_scope", _run_key_scope)
        state["folder"] = folder
        return state

    async def test_the_keys_folders_travel_as_folder_tags(self, brain, scoped_key) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), via_api_key=True)

        await GREP(ctx, {"query": "x"})
        await READ(ctx, {"document_key": "pumps"})

        for _, kwargs in brain["calls"]:
            assert kwargs["access_keys"] == [0, 42]
            assert kwargs["folder_tags"] == [f"folder:{scoped_key['folder']}"]

    async def test_a_key_with_no_folders_reads_nothing(self, brain, scoped_key) -> None:
        from api.services.agents.tools.key_scope import RunKeyScope

        scoped_key["scope"] = RunKeyScope(scoped=True, scopes=frozenset(), masks=(0, 42), folder_ids=frozenset())
        ctx = _FakeCtx(agent=_FakeAgent(), via_api_key=True)

        grep = await GREP(ctx, {"query": "x"})
        read = await READ(ctx, {"document_key": "pumps"})

        assert brain["calls"] == []
        assert "can read no folders" in grep["answer"]
        assert "can read no folders" in read["answer"]

    async def test_a_key_run_cannot_name_another_org(self, brain, scoped_key) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), via_api_key=True, actor_user_id=uuid.uuid4())
        out = await GREP(ctx, {"query": "x", "org": "Other Org"})
        assert "API key" in out["error"]
        assert brain["calls"] == []
