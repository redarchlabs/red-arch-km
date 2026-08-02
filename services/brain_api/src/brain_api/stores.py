"""Lazy-initialized singleton clients for vector, graph, and LLM stores.

These are created on first access (or during lifespan startup) and disposed
at shutdown. Each FastAPI dependency reuses the same client instance.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from brain_sdk.embedding.openai_provider import OpenAIEmbeddingProvider
from brain_sdk.extraction.triplet_extractor import TripletExtractor
from brain_sdk.facts.agent import FactAgent
from brain_sdk.facts.digest import DigestBuilder
from brain_sdk.facts.doc_profiles import DocumentProfiler
from brain_sdk.facts.extraction import ClaimExtractor
from brain_sdk.facts.neo4j_fact_store import Neo4jFactStore
from brain_sdk.facts.pipeline import FactIngestPipeline
from brain_sdk.facts.resolution import EntityResolver, PredicateResolver
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore
from brain_sdk.llm.factory import make_llm_client
from brain_sdk.llm.protocol import LLMClient
from brain_sdk.reranking.http_reranker import HTTPReranker
from brain_sdk.summarization.chunk_summarizer import ChunkSummarizer
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore

from brain_api.config import BrainAPISettings

logger = logging.getLogger(__name__)


class Stores:
    """Container for all externally-connected clients."""

    def __init__(self, settings: BrainAPISettings) -> None:
        self._settings = settings
        # Guards every lazy-init below. Reentrant because some clients are built
        # from others (e.g. ``vector`` reads ``embedder``), so a build holding
        # the lock re-enters it. Without this, two cold requests arriving on
        # worker threads (callers use ``asyncio.to_thread``) could both pass the
        # ``is None`` check and build duplicate Neo4j drivers / OpenAI clients —
        # the loser would be overwritten and leak (never ``close()``d).
        self._lock = threading.RLock()
        self._vector: QdrantVectorStore | None = None
        self._graph: Neo4jGraphStore | None = None
        self._embedder: OpenAIEmbeddingProvider | None = None
        # None is a *valid* built value (reranking unconfigured), so a separate
        # flag rather than the `is None` check every other client uses — without
        # it, an off deployment would re-attempt the build on every search.
        self._reranker: HTTPReranker | None = None
        self._reranker_built = False
        self._summarizer: ChunkSummarizer | None = None
        self._extractor: TripletExtractor | None = None
        # Fact engine (reified-claim store + provider-agnostic LLM + agent).
        self._fact_store: Neo4jFactStore | None = None
        self._llm: LLMClient | None = None
        # Per-model LLM clients for the org model pin (orgs.default_llm_model,
        # threaded in on the agent request). Keyed by model id, built lazily.
        self._model_llms: dict[str, LLMClient] = {}
        self._resolver: EntityResolver | None = None
        self._claim_extractor: ClaimExtractor | None = None
        self._fact_pipeline: FactIngestPipeline | None = None
        self._document_profiler: DocumentProfiler | None = None
        self._predicate_resolver: PredicateResolver | None = None
        self._fact_schema_ready = False

    @property
    def settings(self) -> BrainAPISettings:
        return self._settings

    @property
    def embedder(self) -> OpenAIEmbeddingProvider:
        if self._embedder is None:
            with self._lock:
                if self._embedder is None:
                    self._embedder = OpenAIEmbeddingProvider(
                        api_key=self._settings.openai_api_key,
                        model=self._settings.openai_embedding_model,
                        base_url=self._settings.embedding_base_url,
                        dimension=self._settings.embedding_dimension,
                    )
        return self._embedder

    @property
    def vector(self) -> QdrantVectorStore:
        if self._vector is None:
            with self._lock:
                if self._vector is None:
                    self._vector = QdrantVectorStore(
                        url=self._settings.qdrant_url,
                        api_key=self._settings.qdrant_api_key,
                        chunk_suffix=self._settings.chunk_collection_suffix,
                        document_suffix=self._settings.document_collection_suffix,
                        dimension=self.embedder.dimension,
                    )
        return self._vector

    @property
    def reranker(self) -> HTTPReranker | None:
        """Cross-encoder reranker, or ``None`` when no endpoint is configured.

        Retrieval treats reranking as an enhancement: ``None`` means keep the
        dense order, which is exactly the behaviour before this existed. A
        construction failure (bad URL) degrades the same way rather than taking
        search down with it.
        """
        if not self._reranker_built:
            with self._lock:
                if not self._reranker_built:
                    url = self._settings.rerank_base_url.strip()
                    if url:
                        try:
                            self._reranker = HTTPReranker(
                                base_url=url,
                                model=self._settings.rerank_model,
                                api_key=self._settings.rerank_api_key,
                                timeout=self._settings.rerank_timeout,
                            )
                            logger.info("Reranking enabled via %s", url)
                        except Exception as e:  # noqa: BLE001 - never block search
                            logger.warning("Reranker unavailable (%s); using dense order", e)
                            self._reranker = None
                    self._reranker_built = True
        return self._reranker

    @property
    def graph(self) -> Neo4jGraphStore:
        if self._graph is None:
            with self._lock:
                if self._graph is None:
                    self._graph = Neo4jGraphStore(
                        uri=self._settings.neo4j_uri,
                        user=self._settings.neo4j_user,
                        password=self._settings.neo4j_password,
                    )
        return self._graph

    @property
    def summarizer(self) -> ChunkSummarizer:
        if self._summarizer is None:
            with self._lock:
                if self._summarizer is None:
                    self._summarizer = ChunkSummarizer(
                        api_key=self._settings.openai_api_key,
                        model=self._settings.openai_chat_model,
                        base_url=self._settings.openai_base_url,
                    )
        return self._summarizer

    @property
    def extractor(self) -> TripletExtractor:
        if self._extractor is None:
            with self._lock:
                if self._extractor is None:
                    self._extractor = TripletExtractor(
                        api_key=self._settings.openai_api_key,
                        model=self._settings.openai_chat_model,
                        base_url=self._settings.openai_base_url,
                    )
        return self._extractor

    # ---- fact engine ----------------------------------------------------

    @property
    def predicate_resolver(self) -> PredicateResolver:
        # Shared across the fact store (query-time) and ingest pipeline so the
        # canonical-predicate embedding index is built/cached once.
        if self._predicate_resolver is None:
            with self._lock:
                if self._predicate_resolver is None:
                    self._predicate_resolver = PredicateResolver(self.embedder)
        return self._predicate_resolver

    @property
    def fact_store(self) -> Neo4jFactStore:
        if self._fact_store is None:
            with self._lock:
                if self._fact_store is None:
                    self._fact_store = Neo4jFactStore(
                        uri=self._settings.neo4j_uri,
                        user=self._settings.neo4j_user,
                        password=self._settings.neo4j_password,
                        predicate_normalizer=self.predicate_resolver,
                    )
        return self._fact_store

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            with self._lock:
                if self._llm is None:
                    self._llm = make_llm_client(
                        provider=self._settings.llm_provider,
                        model=self._settings.resolved_agent_model,
                        api_key=self._settings.resolved_agent_api_key,
                        # Fact-engine reasoning is chat, so it follows the chat endpoint.
                        base_url=self._settings.openai_base_url or None,
                    )
        return self._llm

    @property
    def entity_resolver(self) -> EntityResolver:
        if self._resolver is None:
            with self._lock:
                if self._resolver is None:
                    self._resolver = EntityResolver(self.fact_store, self.embedder, llm=self.llm)
        return self._resolver

    @property
    def claim_extractor(self) -> ClaimExtractor:
        if self._claim_extractor is None:
            with self._lock:
                if self._claim_extractor is None:
                    self._claim_extractor = ClaimExtractor(self.llm)
        return self._claim_extractor

    @property
    def fact_pipeline(self) -> FactIngestPipeline:
        if self._fact_pipeline is None:
            with self._lock:
                if self._fact_pipeline is None:
                    self._fact_pipeline = FactIngestPipeline(
                        self.claim_extractor,
                        self.entity_resolver,
                        self.fact_store,
                        predicate_normalizer=self.predicate_resolver,
                        model_name=self._settings.resolved_agent_model,
                    )
        return self._fact_pipeline

    @property
    def document_profiler(self) -> DocumentProfiler:
        """Type-classifier + per-document brief for conditioned extraction.

        Shares the fact engine's LLM so the brief pass reuses one configured
        provider; the profiler runs the brief only when there is sample text.
        """
        if self._document_profiler is None:
            with self._lock:
                if self._document_profiler is None:
                    self._document_profiler = DocumentProfiler(self.llm)
        return self._document_profiler

    def ensure_fact_schema(self) -> None:
        """Create fact-store constraints/indexes once (idempotent)."""
        if self._fact_schema_ready:
            return
        self.fact_store.ensure_schema(embedding_dim=self.embedder.dimension)
        self._fact_schema_ready = True

    def _search_passages(
        self, query: str, limit: int, tenant_id: str, access_keys: tuple[int, ...]
    ) -> list[dict[str, Any]]:
        """Passage-search tool for the agent: embed + Qdrant search → payloads."""
        query_vector = self.embedder.embed(query)
        results = self.vector.search(
            tenant_id=tenant_id,
            query_vector=query_vector,
            limit=limit,
            access_keys=list(access_keys) or None,
        )
        return [
            {
                "document_title": r.payload.get("document_title", "Untitled"),
                "document_key": r.payload.get("document_key", ""),
                "text": r.payload.get("text", ""),
                "score": r.score,
            }
            for r in results
        ]

    def search_passages(
        self, query: str, *, limit: int, tenant_id: str, access_keys: tuple[int, ...] = ()
    ) -> list[dict[str, Any]]:
        """Public passage search (embed + Qdrant) — used to suggest documents to
        re-extract for a knowledge gap."""
        return self._search_passages(query, limit, tenant_id, access_keys)

    def make_digest_builder(self) -> DigestBuilder:
        """Construct a DigestBuilder for (re)building community summaries."""
        return DigestBuilder(self.fact_store, self.llm)

    def llm_for_model(self, model: str | None) -> LLMClient:
        """The fact-engine LLM for a per-request model override, or the default.

        An override is an OpenAI-compatible model id (the per-org pin,
        orgs.default_llm_model) resolved to its endpoint via OPENAI_MODEL_ROUTES
        — so it is always served by the openai provider, even when the
        deployment's default agent LLM is a different provider. Cached per model
        id so repeated requests reuse one client/connection pool.
        """
        if not model or model == self._settings.resolved_agent_model:
            return self.llm
        with self._lock:
            client = self._model_llms.get(model)
            if client is None:
                from brain_api.openai_client import base_url

                url = base_url(self._settings, model)
                client = make_llm_client(
                    provider="openai",
                    model=model,
                    # A routed local endpoint authenticates nothing but the SDK
                    # wants a non-empty key; hosted keeps the real key requirement.
                    api_key=self._settings.openai_api_key or ("not-needed" if url else ""),
                    base_url=url,
                )
                self._model_llms[model] = client
            return client

    def make_fact_agent(self, model: str | None = None) -> FactAgent:
        """Construct a FactAgent wired to the fact store + passage search.

        ``model`` pins the agent's reasoning LLM for this request (the per-org
        model pin); None keeps the deployment default.
        """
        return FactAgent(
            self.llm_for_model(model),
            self.fact_store,
            vector_search=self._search_passages,
            max_iterations=self._settings.agent_max_iterations,
        )

    async def close(self) -> None:
        if self._reranker is not None:
            self._reranker.close()
            self._reranker = None
            self._reranker_built = False
        if self._graph is not None:
            self._graph.close()
            self._graph = None
        if self._fact_store is not None:
            self._fact_store.close()
            self._fact_store = None
        logger.info("Closed brain-api stores")


_stores: Stores | None = None


def get_stores() -> Stores:
    """Return the process-wide Stores singleton."""
    global _stores
    if _stores is None:
        _stores = Stores(BrainAPISettings())  # type: ignore[call-arg]
    return _stores


async def close_stores() -> None:
    global _stores
    if _stores is not None:
        await _stores.close()
        _stores = None
