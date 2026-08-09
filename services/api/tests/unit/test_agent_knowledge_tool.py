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
