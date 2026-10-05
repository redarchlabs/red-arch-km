"""The empty-list contract for retrieval scope (``access_keys`` and ``folder_tags``).

* **absent / null** — no filter: an org admin, an org-wide API key, a trusted
  workflow. Unchanged behaviour.
* **an explicit ``[]``** — a caller whose scope is EMPTY: nothing is readable, so the
  answer is empty and no upstream (embedding, vector store, graph, LLM) is queried.

Before, ``[]`` was folded into "no filter" (``body.access_keys or None``), so any
caller that computed an empty scope and forgot to short-circuit read the whole
tenant. An empty scope now fails closed by construction.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from brain_api.routers import rag, search
from brain_api.routers.agent import AgentAskRequest, agent_ask, agent_ask_stream
from brain_api.routers.rag import AskRequest
from brain_api.routers.search import VectorChatRequest, VectorSearchRequest
from brain_api.services.search_service import SearchService

_BASE = {"tenant_id": "t1", "query": "q"}


@pytest.mark.parametrize("model", [VectorSearchRequest, VectorChatRequest, AskRequest])
def test_absent_is_none_and_empty_stays_empty(model: Any) -> None:
    absent = model(**_BASE)
    assert absent.access_keys is None and absent.folder_tags is None
    explicit = model(**_BASE, access_keys=[], folder_tags=[])
    assert explicit.access_keys == [] and explicit.folder_tags == []
    null = model(**_BASE, access_keys=None, folder_tags=None)
    assert null.access_keys is None and null.folder_tags is None


def test_agent_ask_absent_is_none() -> None:
    assert AgentAskRequest(**_BASE).access_keys is None
    assert AgentAskRequest(**_BASE, access_keys=[]).access_keys == []


@pytest.fixture
def fake_settings() -> MagicMock:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-5-mini"
    settings.rerank_candidates = 30
    settings.chat_chunk_limit = 10
    return settings


@pytest.fixture
def mock_stores() -> MagicMock:
    stores = MagicMock()
    stores.reranker = None
    stores.embedder.embed.return_value = [0.1, 0.2, 0.3]
    stores.vector.search.return_value = []
    stores.vector.list_document_chunks.return_value = []
    return stores


@pytest.fixture
def service(mock_stores: MagicMock, fake_settings: MagicMock) -> SearchService:
    with patch("brain_api.openai_client.OpenAI"):
        svc = SearchService(mock_stores, fake_settings)
    svc._llm = MagicMock()  # noqa: SLF001
    return svc


def _nothing_was_queried(stores: MagicMock, svc: SearchService) -> None:
    stores.embedder.embed.assert_not_called()
    stores.vector.search.assert_not_called()
    stores.graph.fuzzy_relationship_search.assert_not_called()
    svc._llm.chat.completions.create.assert_not_called()  # noqa: SLF001


EMPTY_SCOPES = [{"access_keys": []}, {"folder_tags": []}, {"access_keys": [], "folder_tags": []}]


class TestServiceShortCircuits:
    @pytest.mark.parametrize("scope", EMPTY_SCOPES)
    def test_search_returns_nothing(self, service: SearchService, mock_stores: MagicMock, scope: dict) -> None:
        assert service.vector_search(tenant_id="t1", query="q", **scope) == {"hits": [], "total": 0}
        _nothing_was_queried(mock_stores, service)

    @pytest.mark.parametrize("scope", EMPTY_SCOPES)
    def test_chat_answers_from_nothing(self, service: SearchService, mock_stores: MagicMock, scope: dict) -> None:
        out = service.vector_chat(tenant_id="t1", query="q", **scope)
        assert out["sources"] == [] and out["graph_context"] == []
        assert out["answer"]
        _nothing_was_queried(mock_stores, service)

    @pytest.mark.parametrize("scope", EMPTY_SCOPES)
    def test_stream_answers_from_nothing(self, service: SearchService, mock_stores: MagicMock, scope: dict) -> None:
        events = list(service.vector_chat_stream(tenant_id="t1", query="q", **scope))
        assert [e["type"] for e in events] == ["sources", "graph", "delta", "done"]
        assert events[0]["sources"] == [] and events[1]["triplets"] == []
        _nothing_was_queried(mock_stores, service)

    def test_none_still_means_no_filter(self, service: SearchService, mock_stores: MagicMock) -> None:
        service.vector_search(tenant_id="t1", query="q", access_keys=None, folder_tags=None)
        kwargs = mock_stores.vector.search.call_args.kwargs
        assert kwargs["access_keys"] is None and kwargs["any_tags"] is None


class TestRoutersPassTheScopeThrough:
    """No ``or None`` folding any more: what the caller sent is what the service gets."""

    @pytest.mark.parametrize(("sent", "expected"), [({}, None), ({"access_keys": [], "folder_tags": []}, [])])
    async def test_search(self, sent: dict, expected: Any) -> None:
        svc = MagicMock()
        svc.vector_search.return_value = {"hits": [], "total": 0}
        await search.vector_search(VectorSearchRequest(**_BASE, **sent), service=svc, _api_key="x")
        kwargs = svc.vector_search.call_args.kwargs
        assert kwargs["access_keys"] == expected and kwargs["folder_tags"] == expected

    @pytest.mark.parametrize(("sent", "expected"), [({}, None), ({"access_keys": [], "folder_tags": []}, [])])
    async def test_chat(self, sent: dict, expected: Any) -> None:
        svc = MagicMock()
        svc.vector_chat.return_value = {"answer": "", "sources": [], "graph_context": []}
        await search.vector_chat(VectorChatRequest(**_BASE, **sent), service=svc, _api_key="x")
        kwargs = svc.vector_chat.call_args.kwargs
        assert kwargs["access_keys"] == expected and kwargs["folder_tags"] == expected

    @pytest.mark.parametrize(("sent", "expected"), [({}, None), ({"access_keys": [], "folder_tags": []}, [])])
    async def test_ask(self, sent: dict, expected: Any) -> None:
        svc = MagicMock()
        svc.vector_chat.return_value = {"answer": "", "sources": [], "graph_context": []}
        await rag.ask(AskRequest(**_BASE, **sent), service=svc, _api_key="x")
        kwargs = svc.vector_chat.call_args.kwargs
        assert kwargs["access_keys"] == expected and kwargs["folder_tags"] == expected


class TestAgentAsk:
    async def test_empty_masks_never_run_the_agent(self) -> None:
        stores = MagicMock()
        out = await agent_ask(AgentAskRequest(**_BASE, access_keys=[]), stores=stores, _api_key="x")
        assert out["citations"] == [] and out["evidence"] == []
        stores.make_fact_agent.assert_not_called()

    async def test_empty_masks_stream_a_final_answer_without_the_agent(self) -> None:
        stores = MagicMock()
        resp = await agent_ask_stream(AgentAskRequest(**_BASE, access_keys=[]), stores=stores, _api_key="x")
        chunks = [c async for c in resp.body_iterator]
        events = [json.loads(str(c if isinstance(c, str) else c.decode()).removeprefix("data: ")) for c in chunks]
        assert [e["type"] for e in events] == ["final"]
        stores.make_fact_agent.assert_not_called()

    async def test_absent_masks_are_unrestricted(self) -> None:
        agent = MagicMock()
        agent.run.return_value = MagicMock(
            answer="a", citations=[], unsupported_citations=[], evidence=[{"x": 1}], iterations=1
        )
        stores = MagicMock()
        stores.make_fact_agent.return_value = agent
        await agent_ask(AgentAskRequest(**_BASE), stores=stores, _api_key="x")
        assert agent.run.call_args.args[1].access_keys == ()
