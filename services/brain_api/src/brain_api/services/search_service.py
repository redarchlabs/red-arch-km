"""Search service: vector search and hybrid RAG chat."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from shared_config import get_tracer

from brain_api.config import BrainAPISettings
from brain_api.observability import get_metrics
from brain_api.stores import Stores

logger = logging.getLogger(__name__)
_tracer = get_tracer("brain_api.search")

_RAG_SYSTEM_PROMPT = """\
You are the organization's knowledge-base assistant. Answer questions ONLY \
from the provided context (document passages and knowledge-graph facts).

Each source in the context is a specific passage prefixed with a bracketed \
number, like [1] or [2]. Two passages from the same document have DIFFERENT \
numbers — cite the exact passage a statement came from, not just the document. \
When a statement in your answer comes from a source, cite it inline by \
appending that source's number in brackets right after the statement — e.g. \
"Migrations run before app services [2]." Cite every claim you can, and cite \
multiple sources together when relevant, e.g. "[1][3]". Only use the numbers \
shown in the context; never invent a number. Do NOT append a separate \
"Sources" list at the end of your answer — the interface renders the sources \
separately, so a trailing list would be redundant.

Your general world knowledge must NOT be used to answer questions: when the \
context is empty or does not contain the answer, tell the user that the \
organization's knowledge base has no relevant documents for their question, \
and suggest uploading or pointing you at relevant documents. You may still \
respond naturally to greetings and questions about how to use this assistant.
"""

# Max characters of passage text surfaced to the UI as a citation preview.
# Long enough to show the sentence a citation came from, short enough to keep
# the sources list compact and the SSE payload small.
_SNIPPET_MAX_CHARS = 240


def _snippet(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """Trim passage text to a short preview, breaking on a word boundary.

    Collapses internal whitespace/newlines to single spaces so the preview
    renders cleanly in a one/two-line list item, and appends an ellipsis when
    the passage was truncated.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    cut = collapsed[:max_chars]
    # Prefer the last space so we don't slice a word in half; fall back to a
    # hard cut if there's no space in range.
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + "…"


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
        folder_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Semantic search over chunk vectors.

        ``tags`` are ANDed (every one required). ``folder_tags`` are ORed among
        themselves (the doc must carry at least one) — used to scope retrieval
        to a set of folders without excluding docs that only match one of them.
        """
        metrics = get_metrics()
        start = time.perf_counter()
        status = "success"

        try:
            with _tracer.start_as_current_span(
                "vector_search",
                attributes={"tenant_id": tenant_id, "limit": limit},
            ):
                query_vector = self._stores.embedder.embed(query)
                results = self._stores.vector.search(
                    tenant_id=tenant_id,
                    query_vector=query_vector,
                    limit=limit,
                    access_keys=access_keys,
                    required_tags=tags,
                    any_tags=folder_tags,
                )
        except Exception:
            status = "error"
            raise
        finally:
            # Record duration regardless of outcome so dashboards show both
            # p50/p95 on success and failure timings.
            metrics.search_duration_ms.record(
                (time.perf_counter() - start) * 1000,
                {"tenant_id": tenant_id, "status": status},
            )
        return {
            "hits": [{"id": r.id, "score": r.score, "payload": r.payload} for r in results],
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
        folder_tags: list[str] | None = None,
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
            folder_tags=folder_tags,
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

        # 3. Build LLM prompt (one numbered source per retrieved passage)
        sources = self._passage_sources(hits)
        context_blocks = self._format_context(hits, graph_context)
        messages = cast(
            "list[ChatCompletionMessageParam]",
            self._build_messages(query, chat_history or [], context_blocks),
        )

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
            "sources": sources,
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
        folder_tags: list[str] | None = None,
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
                folder_tags=folder_tags,
            )
            hits = vector_result["hits"]
        except Exception as e:
            logger.error("Vector retrieval failed during stream: %s", e)
            yield {"type": "error", "message": "Retrieval failed"}
            return

        # One numbered source per retrieved passage so the answer's inline [n]
        # citations point at the specific passage, not the whole document.
        sources = self._passage_sources(hits)
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
        messages = cast(
            "list[ChatCompletionMessageParam]",
            self._build_messages(query, chat_history or [], context_blocks),
        )

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

    @staticmethod
    def _passage_sources(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Turn each retrieved chunk into its own numbered passage-level source.

        Retrieval already returns distinct chunks in rank order, so we keep them
        1:1 (no document-level collapse): source ``number`` == the passage's
        position in the context, letting the answer's inline ``[n]`` cite the
        exact passage. Each source carries the passage's ``section`` (heading
        path) and a trimmed ``snippet`` so the UI can show *where* in the
        document the citation came from and deep-link to that chunk.
        """
        sources: list[dict[str, Any]] = []
        for number, hit in enumerate(hits, 1):
            payload = hit["payload"]
            sources.append(
                {
                    "document_id": payload.get("document_id", ""),
                    "document_key": payload.get("document_key", ""),
                    "document_title": payload.get("document_title", ""),
                    "section": payload.get("section"),
                    "chunk_order": payload.get("chunk_order"),
                    "snippet": _snippet(payload.get("text", "")),
                    "score": hit["score"],
                    "number": number,
                }
            )
        return sources

    def _format_context(
        self,
        hits: list[dict[str, Any]],
        graph_context: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []

        # One numbered block per passage — the number matches the source's
        # `number` from `_passage_sources` (both enumerate `hits` in order), so
        # the model's inline [n] lines up with the passage the UI renders.
        if hits:
            parts.append("### Document Passages\n")
            for number, hit in enumerate(hits, 1):
                payload = hit["payload"]
                title = payload.get("document_title", "Untitled")
                section = payload.get("section")
                label = f"{title} — {section}" if section else title
                text = payload.get("text", "")
                parts.append(f"[{number}] {label}\n{text}\n")

        if graph_context:
            parts.append("\n### Knowledge Graph Relationships\n")
            for triplet in graph_context:
                parts.append(f"- {triplet.get('subj', '')} → {triplet.get('pred', '')} → {triplet.get('obj', '')}")

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

        # Always present an explicit context block — omitting it on empty
        # retrieval invites the model to answer from general knowledge.
        effective_context = context if context else "(no relevant documents were found in the knowledge base)"
        user_content = f"Context:\n{effective_context}\n\nQuestion: {query}"
        messages.append({"role": "user", "content": user_content})
        return messages
