"""Lazy-initialized singleton clients for vector, graph, and LLM stores.

These are created on first access (or during lifespan startup) and disposed
at shutdown. Each FastAPI dependency reuses the same client instance.
"""

from __future__ import annotations

import logging

from brain_sdk.embedding.openai_provider import OpenAIEmbeddingProvider
from brain_sdk.extraction.triplet_extractor import TripletExtractor
from brain_sdk.graph_store.neo4j_store import Neo4jGraphStore
from brain_sdk.summarization.chunk_summarizer import ChunkSummarizer
from brain_sdk.vector_store.qdrant_store import QdrantVectorStore

from brain_api.config import BrainAPISettings

logger = logging.getLogger(__name__)


class Stores:
    """Container for all externally-connected clients."""

    def __init__(self, settings: BrainAPISettings) -> None:
        self._settings = settings
        self._vector: QdrantVectorStore | None = None
        self._graph: Neo4jGraphStore | None = None
        self._embedder: OpenAIEmbeddingProvider | None = None
        self._summarizer: ChunkSummarizer | None = None
        self._extractor: TripletExtractor | None = None

    @property
    def embedder(self) -> OpenAIEmbeddingProvider:
        if self._embedder is None:
            self._embedder = OpenAIEmbeddingProvider(
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_embedding_model,
            )
        return self._embedder

    @property
    def vector(self) -> QdrantVectorStore:
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
            self._graph = Neo4jGraphStore(
                uri=self._settings.neo4j_uri,
                user=self._settings.neo4j_user,
                password=self._settings.neo4j_password,
            )
        return self._graph

    @property
    def summarizer(self) -> ChunkSummarizer:
        if self._summarizer is None:
            self._summarizer = ChunkSummarizer(
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_chat_model,
            )
        return self._summarizer

    @property
    def extractor(self) -> TripletExtractor:
        if self._extractor is None:
            self._extractor = TripletExtractor(
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_chat_model,
            )
        return self._extractor

    async def close(self) -> None:
        if self._graph is not None:
            self._graph.close()
            self._graph = None
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
