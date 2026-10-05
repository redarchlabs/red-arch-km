"""Unit tests for what ``search_knowledge`` hands to brain-api.

The mask list *is* the security boundary — brain-api filters on exactly what it
receives, so a bug here is a silent disclosure rather than an error. These pin the
three shapes the tool can produce and, most importantly, that an unattended run
fails closed instead of quietly searching the whole organisation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.services.agents.tools import knowledge

pytestmark = pytest.mark.unit


@dataclass
class _FakeAgent:
    name: str = "engineer"
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


@pytest.fixture
def captured(monkeypatch):
    """Capture the access_keys handed to brain-api without touching the network."""
    seen: dict[str, Any] = {}

    class _Client:
        def __init__(self, _settings): ...

        async def vector_chat(self, **kwargs):
            seen.update(kwargs)
            return {"answer": "ok", "sources": []}

    async def _model(_session, _org):
        return None

    async def _keys(_session, _org, profile_id):
        return seen.get("_resolved", [0, 99])

    monkeypatch.setattr("api.services.brain_client.BrainAPIClient", _Client)
    monkeypatch.setattr("api.services.org_llm.org_default_llm_model", _model)
    monkeypatch.setattr("api.services.search_access.resolve_profile_access_keys", _keys)
    return seen


class TestActorScoping:
    async def test_an_actors_masks_are_passed_through(self, captured) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "what is the policy?"})

        assert out["answer"] == "ok"
        assert captured["access_keys"] == [0, 99]

    async def test_an_actor_with_no_membership_is_refused(self, captured) -> None:
        """``[]`` means "nothing readable" and must not be confused with ``None``
        (unrestricted) — passing it on would disable the filter entirely."""
        captured["_resolved"] = []
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "no knowledge-base access" in out["error"]
        assert "access_keys" not in captured  # never reached brain-api


class TestUnattendedRuns:
    async def test_a_run_with_no_actor_fails_closed(self, captured) -> None:
        """A schedule or inbound webhook has nobody to inherit from. The old
        behaviour — search the whole org — is exactly the disclosure this closes."""
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=None)

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "no user to read on behalf of" in out["error"]
        assert "knowledge_scope" in out["error"]  # tells the admin how to opt in
        assert "access_keys" not in captured

    async def test_org_scope_is_an_explicit_opt_in(self, captured) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(grants={"knowledge_scope": "org"}), actor_user_id=None)

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert out["answer"] == "ok"
        assert captured["access_keys"] is None  # unrestricted, deliberately

    async def test_an_actor_still_wins_over_org_scope(self, captured) -> None:
        """``knowledge_scope: org`` covers the *unattended* case only. It must not
        widen a run that does have an actor, or it becomes a privilege escalation
        for every console user of that agent."""
        ctx = _FakeCtx(agent=_FakeAgent(grants={"knowledge_scope": "org"}), actor_user_id=uuid.uuid4())

        await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert captured["access_keys"] == [0, 99]


class TestValidation:
    async def test_an_empty_query_is_refused(self, captured) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "   "})

        assert "required" in out["error"]


class TestWhenTheServiceIsDown:
    """What the model *does* with the error matters more than what it says.

    The raw exception was an httpx repr — a 500, an internal URL and a link to the
    httpx docs. An agent reading that concluded it had no way to search local
    knowledge at all, asked the person who filed the order for "the exact
    knowledge-graph or internal host so I can query it directly", and parked the
    order on a request nobody can fulfil. The search layer was simply down.
    """

    @pytest.fixture
    def broken(self, monkeypatch):
        class _Client:
            def __init__(self, _settings): ...

            async def vector_chat(self, **kwargs):
                raise RuntimeError(
                    "Server error '500 Internal Server Error' for url "
                    "'http://localhost:8020/api/vector-chat'\nFor more information check: "
                    "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500"
                )

        async def _model(_session, _org):
            return None

        async def _keys(_session, _org, profile_id):
            return [0, 99]

        monkeypatch.setattr("api.services.brain_client.BrainAPIClient", _Client)
        monkeypatch.setattr("api.services.org_llm.org_default_llm_model", _model)
        monkeypatch.setattr("api.services.search_access.resolve_profile_access_keys", _keys)

    async def test_it_says_the_platform_broke_not_that_the_agent_cannot(self, broken) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "fault in the platform" in out["error"]
        assert "You DO have" in out["error"]

    async def test_it_forbids_asking_a_person_for_a_host(self, broken) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "Do not ask a person for a host" in out["error"]

    async def test_the_internal_url_does_not_reach_the_model(self, broken) -> None:
        # The URL is what the agent latched onto. It belongs in the log, not the
        # transcript — and never in a question put to a person.
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "localhost:8020" not in out["error"]
        assert "http" not in out["error"]


class TestTheLocalOrgIsTheDefault:
    """Cross-org reach exists, but only on an explicit instruction.

    Pulling another org's material into this one's work order writes it into this
    org's diary and artifacts, where it stays. That crossing should happen because
    a person asked for it, not because a retrieval came back thin.
    """

    async def test_no_org_argument_searches_the_agents_own_org(self, captured) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert captured["tenant_id"] == str(ctx.org_id)

    def test_the_tool_tells_the_model_its_own_org_is_the_default(self) -> None:
        text = knowledge.SEARCH_KNOWLEDGE.description
        assert "YOUR OWN org" in text
        assert "ONLY when someone has asked you" in text

    async def test_an_unattended_run_cannot_cross_orgs(self, captured) -> None:
        """``knowledge_scope: "org"`` is a grant about *this* org. If it carried
        across tenants, every schedule and webhook would become a system reader."""
        ctx = _FakeCtx(agent=_FakeAgent(grants={"knowledge_scope": "org"}), actor_user_id=None)

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x", "org": "Come Follow Me"})

        assert "cannot search another" in out["error"]
        assert "tenant_id" not in captured  # never reached brain-api


class TestNamingTheOrgThatAnswered:
    """An agent told "check the come follow me org" searched its own, found nothing,
    and reported the material missing from the org it never touched — an empty
    result from one tenant offered as proof of absence in another. The result now
    carries the org that answered, so the model cannot attribute it elsewhere."""

    async def test_the_result_names_the_org_searched(self, captured) -> None:
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert out["searched_org"] == str(ctx.org_id)  # no session to resolve a name

    def test_the_tool_forbids_claiming_an_org_it_did_not_search(self) -> None:
        assert "never claim you searched one you did not" in knowledge.SEARCH_KNOWLEDGE.description


class TestMatchingAnOrgByName:
    """People say org names loosely ("in the come follow me org"). Matching is
    forgiving about that, and strict about ties."""

    class _Org:
        def __init__(self, name: str, oid: uuid.UUID | None = None) -> None:
            self.name = name
            self.id = oid or uuid.uuid4()

    def test_a_spoken_name_with_a_trailing_org_still_matches(self) -> None:
        cfm = self._Org("Come Follow Me")

        assert knowledge._match_orgs([cfm, self._Org("Robots")], "come follow me org") == [cfm]

    def test_an_exact_name_beats_an_org_that_contains_it(self) -> None:
        """ "Robots" is a substring of "Robots (OpenAI)" — naming it exactly must
        still resolve to it rather than reporting an ambiguous tie."""
        robots = self._Org("Robots")

        assert knowledge._match_orgs([robots, self._Org("Robots (OpenAI)")], "Robots") == [robots]

    def test_an_ambiguous_name_returns_every_candidate(self) -> None:
        orgs = [self._Org("Robots (OpenAI)"), self._Org("Robots (Local)")]

        assert len(knowledge._match_orgs(orgs, "robots")) == 2

    def test_an_org_id_resolves_directly(self) -> None:
        wanted = self._Org("Come Follow Me")

        assert knowledge._match_orgs([wanted, self._Org("Robots")], str(wanted.id)) == [wanted]

    def test_an_unknown_name_matches_nothing(self) -> None:
        assert knowledge._match_orgs([self._Org("Robots")], "Payroll") == []


class TestRunsStartedByAnApiKey:
    """A run started through an API key has no actor and reads with the KEY's scope.

    The key is reloaded on every call (``run_key_scope``). A scoped key's masks and
    folders are used even when the agent holds ``knowledge_scope: "org"`` — that
    branch would otherwise make an actor-less run unrestricted."""

    @pytest.fixture
    def key(self, monkeypatch):
        from api.services.agents.tools.key_scope import RunKeyScope

        state: dict[str, Any] = {
            "scope": RunKeyScope(scoped=True, scopes=frozenset({"agents:run"}), masks=(0, 7, 8), folder_ids=None)
        }

        async def _scope(_ctx):
            if isinstance(state["scope"], Exception):
                raise state["scope"]
            return state["scope"]

        monkeypatch.setattr(knowledge, "run_key_scope", _scope)
        return state

    def _ctx(self, **grants: Any) -> _FakeCtx:
        return _FakeCtx(agent=_FakeAgent(grants=grants), via_api_key=True, api_key_id=uuid.uuid4())

    async def test_cannot_search_another_org(self, captured, monkeypatch, key) -> None:
        resolver_called = False

        async def _resolve(_ctx, _requested):
            nonlocal resolver_called
            resolver_called = True
            return {"org_id": uuid.uuid4(), "name": "Other", "access_keys": None, "model": None}

        monkeypatch.setattr(knowledge, "_resolve_named_org", _resolve)
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(), {"query": "x", "org": "Other"})

        assert "error" in out
        assert not resolver_called
        assert "access_keys" not in captured

    async def test_scoped_key_reads_with_the_keys_masks(self, captured, key) -> None:
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(), {"query": "x"})

        assert out["answer"] == "ok"
        assert captured["access_keys"] == [0, 7, 8]
        assert captured["folder_tags"] is None

    async def test_org_scope_grant_cannot_make_a_scoped_key_run_unrestricted(self, captured, key) -> None:
        """The hole this closes: actor None + knowledge_scope "org" = access_keys None."""
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert out["answer"] == "ok"
        assert captured["access_keys"] == [0, 7, 8]

    async def test_folder_limited_key_always_sends_its_folders(self, captured, key) -> None:
        from api.services.agents.tools.key_scope import RunKeyScope

        f1, f2 = uuid.uuid4(), uuid.uuid4()
        key["scope"] = RunKeyScope(scoped=True, scopes=frozenset(), masks=(0, 7), folder_ids=frozenset({f1, f2}))
        await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert sorted(captured["folder_tags"]) == sorted([f"folder:{f1}", f"folder:{f2}"])

    async def test_empty_folder_set_never_reaches_brain(self, captured, key) -> None:
        """[] folder_tags means "no folder filter" downstream: answer empty instead."""
        from api.services.agents.tools.key_scope import RunKeyScope

        key["scope"] = RunKeyScope(scoped=True, scopes=frozenset(), masks=(0, 7), folder_ids=frozenset())
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert out["sources"] == []
        assert "access_keys" not in captured

    @pytest.mark.parametrize("masks", [(), None])
    async def test_scoped_key_without_masks_fails_closed(self, captured, key, masks) -> None:
        from api.services.agents.tools.key_scope import RunKeyScope

        key["scope"] = RunKeyScope(scoped=True, scopes=frozenset(), masks=masks, folder_ids=None)
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert "error" in out
        assert "access_keys" not in captured

    async def test_revoked_or_deleted_key_is_refused(self, captured, key) -> None:
        from api.services.agents.tools.key_scope import RunKeyRefused

        key["scope"] = RunKeyRefused("gone")
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert out == {"error": "gone"}
        assert "access_keys" not in captured

    async def test_org_key_run_keeps_the_unattended_rule(self, captured, key) -> None:
        """An org key's run behaves like any unattended run: refused without the
        grant, org-wide with it (an explicit admin decision about the agent)."""
        from api.services.agents.tools.key_scope import RunKeyScope

        key["scope"] = RunKeyScope(scoped=False, scopes=frozenset())
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(), {"query": "x"})
        assert "error" in out and "access_keys" not in captured

        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})
        assert out["answer"] == "ok"
        assert captured["access_keys"] is None

    async def test_a_key_run_never_reads_unrestricted_through_an_actor(self, captured, key) -> None:
        """A person starting a key-filed order gives the run an actor; an org key's run
        still never borrows that person's (admin) reach — without the agent's grant it
        is refused like any unattended run."""
        from api.services.agents.tools.key_scope import RunKeyScope

        key["scope"] = RunKeyScope(scoped=False, scopes=frozenset())
        captured["_resolved"] = None  # resolve_profile_access_keys → unrestricted
        ctx = self._ctx()
        ctx.actor_user_id = uuid.uuid4()
        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "error" in out
        assert "access_keys" not in captured


class TestAPersonStartingAKeyFiledOrder:
    """A work order filed through an API key, started by a person: the run carries
    both the key and an actor. The KEY decides — an org key reads exactly like the
    unattended run it would otherwise be (the actor is ignored, admin or not); a
    scoped key reads with its own masks and folders."""

    @pytest.fixture
    def key(self, monkeypatch):
        from api.services.agents.tools.key_scope import RunKeyScope

        state: dict[str, Any] = {"scope": RunKeyScope(scoped=False, scopes=frozenset({"work_orders:write"}))}

        async def _scope(_ctx):
            return state["scope"]

        monkeypatch.setattr(knowledge, "run_key_scope", _scope)
        return state

    def _ctx(self, **grants: Any) -> _FakeCtx:
        return _FakeCtx(
            agent=_FakeAgent(grants=grants), via_api_key=True, api_key_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )

    @pytest.mark.parametrize("actor_reach", [None, [0, 99]], ids=["admin-actor", "restricted-actor"])
    async def test_org_key_with_the_grant_reads_org_wide(self, captured, key, actor_reach) -> None:
        captured["_resolved"] = actor_reach
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert out["answer"] == "ok"
        assert captured["access_keys"] is None  # the org key's reach, not the actor's

    async def test_org_key_without_the_grant_is_refused_like_an_unattended_run(self, captured, key) -> None:
        captured["_resolved"] = [0, 99]
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(), {"query": "x"})

        assert "knowledge_scope" in out["error"]
        assert "cannot read unrestricted" not in out["error"]
        assert "access_keys" not in captured

    async def test_scoped_key_reads_with_its_own_masks_whoever_started_it(self, captured, key) -> None:
        from api.services.agents.tools.key_scope import RunKeyScope

        key["scope"] = RunKeyScope(scoped=True, scopes=frozenset(), masks=(0, 7), folder_ids=None)
        captured["_resolved"] = None  # an admin actor
        out = await knowledge.SEARCH_KNOWLEDGE.handler(self._ctx(knowledge_scope="org"), {"query": "x"})

        assert out["answer"] == "ok"
        assert captured["access_keys"] == [0, 7]


class TestTooManyMasks:
    async def test_an_actor_over_the_mask_cap_gets_an_error_not_a_crash(self, captured, monkeypatch) -> None:
        from api.services.permission_config import TooManyAccessMasks

        async def _too_many(_session, _org, _profile):
            raise TooManyAccessMasks(20_000)

        monkeypatch.setattr("api.services.search_access.resolve_profile_access_keys", _too_many)
        ctx = _FakeCtx(agent=_FakeAgent(), actor_user_id=uuid.uuid4())

        out = await knowledge.SEARCH_KNOWLEDGE.handler(ctx, {"query": "x"})

        assert "too many" in out["error"]
        assert "access_keys" not in captured
