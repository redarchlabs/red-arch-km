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
        self._summarizer: ChunkSummarizer | None = None
        self._extractor: TripletExtractor | None = None
        # Fact engine (reified-claim store + provider-agnostic LLM + agent).
        self._fact_store: Neo4jFactStore | None = None
        self._llm: LLMClient | None = None
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

    def make_digest_builder(self) -> DigestBuilder:
        """Construct a DigestBuilder for (re)building community summaries."""
        return DigestBuilder(self.fact_store, self.llm)

    def make_fact_agent(self) -> FactAgent:
        """Construct a FactAgent wired to the fact store + passage search."""
        return FactAgent(
            self.llm,
            self.fact_store,
            vector_search=self._search_passages,
            max_iterations=self._settings.agent_max_iterations,
        )

    async def close(self) -> None:
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
