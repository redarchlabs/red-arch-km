"""Search service: vector search and hybrid RAG chat."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from openai import OpenAI

from brain_api.config import BrainAPISettings
from brain_api.stores import Stores

logger = logging.getLogger(__name__)

_RAG_SYSTEM_PROMPT = """\
You are a knowledgeable assistant answering questions using the provided context.
Cite sources by referencing document titles when possible.
If the context does not contain the answer, say so clearly rather than guessing.
"""


class SearchService:
    """Vector search and hybrid RAG chat."""

    def __init__(self, stores: Stores, settings: BrainAPISettings) -> None:
        self._stores = stores
        self._settings = settings
        self._llm = OpenAI(api_key=settings.openai_api_key)

    def vector_search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 5,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Semantic search over chunk vectors."""
        query_vector = self._stores.embedder.embed(query)
        results = self._stores.vector.search(
            tenant_id=tenant_id,
            query_vector=query_vector,
            limit=limit,
            access_keys=access_keys,
            required_tags=tags,
        )
        return {
            "hits": [
                {"id": r.id, "score": r.score, "payload": r.payload}
                for r in results
            ],
            "total": len(results),
        }

    def vector_chat(
        self,
        *,
        tenant_id: str,
        query: str,
        chat_history: list[dict[str, str]] | None = None,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
        use_knowledge_graph: bool = True,
        chunk_limit: int = 5,
    ) -> dict[str, Any]:
        """Hybrid RAG: vector retrieval + optional graph context → LLM synthesis."""
        # 1. Vector retrieval
        vector_result = self.vector_search(
            tenant_id=tenant_id,
            query=query,
            limit=chunk_limit,
            access_keys=access_keys,
            tags=tags,
        )
        hits = vector_result["hits"]

        # 2. Optional graph context
        graph_context: list[dict[str, Any]] = []
        if use_knowledge_graph:
            try:
                graph_context = self._stores.graph.fuzzy_relationship_search(
                    tenant_id=tenant_id,
                    term=query,
                    tags=tags,
                    user_access=access_keys,
                )[:10]
            except Exception as e:
                logger.warning("Graph search failed: %s", e)

        # 3. Build LLM prompt
        context_blocks = self._format_context(hits, graph_context)
        messages = self._build_messages(query, chat_history or [], context_blocks)

        # 4. Synthesize answer
        try:
            response = self._llm.chat.completions.create(
                model=self._settings.openai_chat_model,
                messages=messages,
                max_tokens=1000,
                temperature=0.3,
            )
            answer = response.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM chat completion failed: %s", e)
            answer = "I'm sorry, I encountered an error generating a response."

        return {
            "answer": answer,
            "sources": [
                {
                    "document_id": h["payload"].get("document_id", ""),
                    "document_key": h["payload"].get("document_key", ""),
                    "document_title": h["payload"].get("document_title", ""),
                    "text": h["payload"].get("text", "")[:500],
                    "score": h["score"],
                }
                for h in hits
            ],
            "graph_context": graph_context,
        }

    def vector_chat_stream(
        self,
        *,
        tenant_id: str,
        query: str,
        chat_history: list[dict[str, str]] | None = None,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
        use_knowledge_graph: bool = True,
        chunk_limit: int = 5,
    ) -> Iterator[dict[str, Any]]:
        """Streaming hybrid RAG chat.

        Yields event dicts with `type` in {"sources", "graph", "delta", "done", "error"}.
        The caller is responsible for serialising events to the wire format (e.g. SSE).
        """
        # 1. Vector retrieval
        try:
            vector_result = self.vector_search(
                tenant_id=tenant_id,
                query=query,
                limit=chunk_limit,
                access_keys=access_keys,
                tags=tags,
            )
            hits = vector_result["hits"]
        except Exception as e:
            logger.error("Vector retrieval failed during stream: %s", e)
            yield {"type": "error", "message": "Retrieval failed"}
            return

        sources = [
            {
                "document_id": h["payload"].get("document_id", ""),
                "document_key": h["payload"].get("document_key", ""),
                "document_title": h["payload"].get("document_title", ""),
                "score": h["score"],
            }
            for h in hits
        ]
        yield {"type": "sources", "sources": sources}

        # 2. Optional graph context
        graph_context: list[dict[str, Any]] = []
        if use_knowledge_graph:
            try:
                graph_context = self._stores.graph.fuzzy_relationship_search(
                    tenant_id=tenant_id,
                    term=query,
                    tags=tags,
                    user_access=access_keys,
                )[:10]
            except Exception as e:
                logger.warning("Graph search failed during stream: %s", e)
        yield {"type": "graph", "triplets": graph_context}

        # 3. Stream LLM completion
        context_blocks = self._format_context(hits, graph_context)
        messages = self._build_messages(query, chat_history or [], context_blocks)

        try:
            stream = self._llm.chat.completions.create(
                model=self._settings.openai_chat_model,
                messages=messages,
                max_tokens=1000,
                temperature=0.3,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield {"type": "delta", "content": delta}
        except Exception as e:
            logger.error("LLM stream failed: %s", e)
            yield {"type": "error", "message": "Streaming failed"}
            return

        yield {"type": "done"}

    def _format_context(
        self,
        hits: list[dict[str, Any]],
        graph_context: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []

        if hits:
            parts.append("### Document Excerpts\n")
            for i, hit in enumerate(hits, 1):
                payload = hit["payload"]
                title = payload.get("document_title", "Untitled")
                text = payload.get("text", "")
                parts.append(f"[Source {i} — {title}]\n{text}\n")

        if graph_context:
            parts.append("\n### Knowledge Graph Relationships\n")
            for triplet in graph_context:
                parts.append(
                    f"- {triplet.get('subj', '')} → {triplet.get('pred', '')} → {triplet.get('obj', '')}"
                )

        return "\n".join(parts)

    def _build_messages(
        self,
        query: str,
        chat_history: list[dict[str, str]],
        context: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": _RAG_SYSTEM_PROMPT}]

        # Clamp history to last 10 turns to control context size
        for turn in chat_history[-10:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        user_content = f"Context:\n{context}\n\nQuestion: {query}" if context else query
        messages.append({"role": "user", "content": user_content})
        return messages
