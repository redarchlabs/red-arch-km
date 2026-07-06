"""Unit tests for the agentic query loop (scripted LLM + fake store)."""

from __future__ import annotations

import json
from typing import Any

from brain_sdk.facts.agent import AgentContext, FactAgent
from brain_sdk.facts.models import Entity


class ScriptedLLM:
    """Returns queued JSON responses in order (repeats the last)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._i = 0
        self.seen: list[list[Any]] = []

    @property
    def model(self) -> str:
        return "scripted"

    def complete(self, messages, *, temperature=0.2, max_tokens=1024, json_object=False):  # type: ignore[no-untyped-def]
        self.seen.append(messages)
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return r


class FakeAgentStore:
    def __init__(self) -> None:
        self._by_name: dict[str, Entity] = {}
        self._claims: dict[str, list[dict[str, Any]]] = {}
        self._nbr: dict[str, list[dict[str, Any]]] = {}
        self.tenants_seen: list[str] = []

    def add_entity(self, e: Entity) -> None:
        self._by_name[e.canonical_name.lower()] = e

    def set_claims(self, entity_id: str, rows: list[dict[str, Any]]) -> None:
        self._claims[entity_id] = rows

    def find_entities(self, tenant_id, *, name=None, embedding=None, limit=10):  # type: ignore[no-untyped-def]
        self.tenants_seen.append(tenant_id)
        e = self._by_name.get((name or "").lower())
        return [(e, 1.0)] if e else []

    def query_claims(self, tenant_id, *, subject_id=None, predicate=None, object_value=None,  # type: ignore[no-untyped-def]
                     as_of=None, statuses=None, access_keys=None, limit=100):
        self.tenants_seen.append(tenant_id)
        return self._claims.get(subject_id, [])

    def neighborhood(self, tenant_id, entity_id, *, hops=1, access_keys=None, limit=100):  # type: ignore[no-untyped-def]
        return self._nbr.get(entity_id, [])

    def get_communities(self, tenant_id, *, limit=20):  # type: ignore[no-untyped-def]
        self.tenants_seen.append(tenant_id)
        return [{"community_id": "t1:c1", "summary": "Acme and its subsidiaries.", "size": 3}]


def _acme_store() -> tuple[FakeAgentStore, Entity]:
    store = FakeAgentStore()
    acme = Entity.make(tenant_id="t1", canonical_name="Acme", type="ORG")
    store.add_entity(acme)
    store.set_claims(
        acme.entity_id,
        [{
            "subject": "Acme", "predicate": "headquartered_in", "object": "Paris",
            "status": "active", "confidence": 1.0,
        }],
    )
    return store, acme


class TestAgentLoop:
    def test_tool_then_final(self) -> None:
        store, _ = _acme_store()
        llm = ScriptedLLM(
            [
                json.dumps(
                    {"thought": "look up HQ", "action": {"tool": "claim_query",
                     "args": {"subject": "Acme", "predicate": "headquartered_in"}}}
                ),
                json.dumps({"thought": "answer", "final": {"answer": "Acme is in Paris [E1].", "citations": ["E1"]}}),
            ]
        )
        agent = FactAgent(llm, store)  # type: ignore[arg-type]
        result = agent.run("Where is Acme HQ?", AgentContext(tenant_id="t1"))

        assert "Paris" in result.answer
        assert result.citations == ["E1"]
        assert result.unsupported_citations == []
        assert len(result.evidence) == 1
        assert result.evidence[0]["tool"] == "claim_query"
        types = [e["type"] for e in result.trace]
        assert "tool_call" in types and "tool_result" in types and "final" in types

    def test_unsupported_citation_flagged(self) -> None:
        store, _ = _acme_store()
        llm = ScriptedLLM(
            [json.dumps({"thought": "guess", "final": {"answer": "Paris [E9].", "citations": ["E9"]}})]
        )
        agent = FactAgent(llm, store)  # type: ignore[arg-type]
        result = agent.run("Where is Acme HQ?", AgentContext(tenant_id="t1"))
        # E9 was never gathered → flagged as unsupported (grounding check).
        assert result.unsupported_citations == ["E9"]

    def test_tenant_id_passed_to_store(self) -> None:
        store, _ = _acme_store()
        llm = ScriptedLLM(
            [
                json.dumps({"thought": "x", "action": {"tool": "entity_lookup", "args": {"name": "Acme"}}}),
                json.dumps({"thought": "y", "final": {"answer": "ok [E1]", "citations": ["E1"]}}),
            ]
        )
        agent = FactAgent(llm, store)  # type: ignore[arg-type]
        agent.run("q", AgentContext(tenant_id="tenant-xyz"))
        assert all(t == "tenant-xyz" for t in store.tenants_seen)

    def test_budget_exhaustion_forces_answer(self) -> None:
        store, _ = _acme_store()
        # Model never finishes — always asks for another tool call.
        loop_action = json.dumps(
            {"thought": "again", "action": {"tool": "claim_query", "args": {"subject": "Acme"}}}
        )
        llm = ScriptedLLM([loop_action])
        agent = FactAgent(llm, store, max_iterations=2)  # type: ignore[arg-type]
        result = agent.run("q", AgentContext(tenant_id="t1"))
        assert result.answer  # a best-effort answer is always produced
        assert result.iterations == 2

    def test_search_passages_unavailable_without_backend(self) -> None:
        store, _ = _acme_store()
        llm = ScriptedLLM(
            [
                json.dumps({"thought": "x", "action": {"tool": "search_passages", "args": {"query": "acme"}}}),
                json.dumps({"thought": "y", "final": {"answer": "no data [E1]", "citations": ["E1"]}}),
            ]
        )
        agent = FactAgent(llm, store)  # type: ignore[arg-type]  # no vector_search wired
        result = agent.run("q", AgentContext(tenant_id="t1"))
        tool_results = [e for e in result.trace if e["type"] == "tool_result"]
        assert tool_results[0]["records"] == []

    def test_corpus_overview_tool_reads_communities(self) -> None:
        store, _ = _acme_store()
        llm = ScriptedLLM(
            [
                json.dumps({"thought": "orient", "action": {"tool": "corpus_overview", "args": {}}}),
                json.dumps({"thought": "answer", "final": {"answer": "It's about Acme [E1].", "citations": ["E1"]}}),
            ]
        )
        agent = FactAgent(llm, store)  # type: ignore[arg-type]
        result = agent.run("What is this corpus about?", AgentContext(tenant_id="t1"))
        overview = next(e for e in result.trace if e["type"] == "tool_result")
        assert overview["records"][0]["summary"] == "Acme and its subsidiaries."

    def test_vector_search_tool_invoked(self) -> None:
        store, _ = _acme_store()
        captured: dict[str, Any] = {}

        def fake_vs(query: str, limit: int, tenant_id: str, access_keys: tuple[int, ...]) -> list[dict[str, Any]]:
            captured["tenant_id"] = tenant_id
            return [{"document_title": "Doc", "text": "Acme is based in Paris."}]

        llm = ScriptedLLM(
            [
                json.dumps({"thought": "x", "action": {"tool": "search_passages", "args": {"query": "acme hq"}}}),
                json.dumps({"thought": "y", "final": {"answer": "Paris [E1]", "citations": ["E1"]}}),
            ]
        )
        agent = FactAgent(llm, store, vector_search=fake_vs)  # type: ignore[arg-type]
        result = agent.run("q", AgentContext(tenant_id="t-vs"))
        assert captured["tenant_id"] == "t-vs"  # tenant injected server-side
        assert result.evidence[0]["result"][0]["document_title"] == "Doc"
