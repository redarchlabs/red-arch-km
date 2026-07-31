"""Search service: vector search and hybrid RAG chat."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any, cast

from brain_sdk.reranking.protocol import Reranker
from openai.types.chat import ChatCompletionMessageParam
from shared_config import get_tracer

from brain_api.config import BrainAPISettings
from brain_api.observability import get_metrics
from brain_api.openai_client import make_openai
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

# --- same-document expansion -------------------------------------------- #
# Dense top-k ranks a passage that *describes* something above the passage that
# *contains* it: "what are the names of all six ships" matches the section
# introducing the fleet, while the table listing the six names ranks ~37th. So
# after ranking, the best-scoring document's remaining chunks are pulled in as
# context even though they didn't rank on their own.
#
# How many top-ranked documents get expanded. One keeps the added context tightly
# focused on the single best match; raising this dilutes the prompt fast.
_EXPAND_TOP_DOCS = 1
# Character budget for ADDED sibling text (~1.5k tokens). A typical KB article
# fits whole; a long one contributes its opening sections and stops.
_EXPAND_CHAR_BUDGET = 6000
# Hard cap on siblings fetched per document, so a pathologically chunked
# document can't turn one query into a huge scroll.
_EXPAND_MAX_CHUNKS = 40


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
        # Honours OPENAI_BASE_URL so this instance's chat can run against a local
        # OpenAI-compatible server; unset, this is exactly OpenAI(api_key=…) as before.
        self._llm = make_openai(settings, settings.openai_api_key)

    def warm_up(self) -> None:
        """Exercise the read path once so the first *real* user query is warm.

        Store construction (done in the app lifespan) only builds the clients; the
        first real embedding, Qdrant search, Neo4j query, and chat completion each
        pay a one-time connection/TLS/pool cost — observed as ~20s cold vs ~3s warm
        on the robot's first question. Issue a tiny throwaway of each against a
        synthetic tenant (retrieval returns empty, mutates nothing) so a visitor's
        first turn doesn't absorb that. Best-effort: every probe is isolated and a
        failure only logs — warm-up must never keep the service from starting.
        """
        probe = "warm up probe"
        tenant = "__warmup__"  # no such tenant: search returns empty / 404, writes nothing
        try:
            query_vector = self._stores.embedder.embed(probe)
            self._stores.vector.search(tenant_id=tenant, query_vector=query_vector, limit=1)
        except Exception as e:  # noqa: BLE001 - warm-up is best-effort
            logger.info("warm-up retrieval path skipped: %s", e)
        try:
            self._stores.graph.fuzzy_relationship_search(tenant, probe)
        except Exception as e:  # noqa: BLE001
            logger.info("warm-up graph path skipped: %s", e)
        try:
            self._llm.chat.completions.create(
                model=self._settings.openai_chat_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
        except Exception as e:  # noqa: BLE001
            logger.info("warm-up chat path skipped: %s", e)

    def vector_search(
        self,
        *,
        tenant_id: str,
        query: str,
        limit: int = 5,
        access_keys: list[int] | None = None,
        tags: list[str] | None = None,
        folder_tags: list[str] | None = None,
        expand_documents: bool = True,
    ) -> dict[str, Any]:
        """Semantic search over chunk vectors.

        ``tags`` are ANDed (every one required). ``folder_tags`` are ORed among
        themselves (the doc must carry at least one) — used to scope retrieval
        to a set of folders without excluding docs that only match one of them.

        ``expand_documents`` (default on) additionally pulls the top-ranked
        document's other chunks in reading order — see
        :meth:`_expand_top_documents`. Pass ``False`` for a pure ranked-hits
        view (e.g. relevance debugging).

        When a reranker is configured, a wider shortlist is fetched from the
        vector store and re-scored down to ``limit`` before expansion — see
        :meth:`_rerank_hits`. Expansion then follows the *reranked* leader, which
        is the point: it is what puts the answering document in the prompt.
        """
        metrics = get_metrics()
        start = time.perf_counter()
        status = "success"
        reranker = self._stores.reranker
        # Over-fetch only when something will re-score it; otherwise the vector
        # store sees exactly the request it always saw.
        fetch = max(limit, self._settings.rerank_candidates) if reranker else limit

        try:
            with _tracer.start_as_current_span(
                "vector_search",
                attributes={"tenant_id": tenant_id, "limit": limit, "candidates": fetch},
            ):
                query_vector = self._stores.embedder.embed(query)
                results = self._stores.vector.search(
                    tenant_id=tenant_id,
                    query_vector=query_vector,
                    limit=fetch,
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
        hits = [{"id": r.id, "score": r.score, "payload": r.payload} for r in results]
        if reranker is not None:
            hits = self._rerank_hits(reranker, query, hits, limit)
        if expand_documents:
            hits = self._expand_top_documents(
                tenant_id,
                hits,
                access_keys=access_keys,
                tags=tags,
                folder_tags=folder_tags,
            )
        return {"hits": hits, "total": len(hits)}

    def _resolve_chunk_limit(self, chunk_limit: int | None) -> int:
        """How many ranked passages ground an answer: the caller's value, else config.

        Kept as a resolve-at-call-time lookup rather than a signature default so the
        two chat entry points cannot drift apart, and so the setting can be changed
        without touching either one.
        """
        return chunk_limit if chunk_limit is not None else self._settings.chat_chunk_limit

    def _rerank_hits(
        self,
        reranker: Reranker,
        query: str,
        hits: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Re-score the dense shortlist with a cross-encoder; keep the best ``limit``.

        Dense retrieval embeds query and passage independently, so it ranks by
        topical similarity and cannot see that "how many people can each ship
        handle" is answered by "**Standard Crew Complement:** 5,500 officers and
        crew" — no shared vocabulary, not near neighbours. Those passages sat
        outside the top 5 while a booking FAQ about crew limits sat inside it. A
        cross-encoder reads query and passage together and scores that pair.

        The dense score is kept as ``dense_score`` so the two rankings can be
        compared when debugging relevance; ``score`` becomes the rerank score,
        which is what every downstream consumer orders by.

        Never raises: a reranker that is down, slow, or misconfigured degrades to
        the dense top-``limit`` — the exact behaviour from before it existed.
        """
        if not hits:
            return hits

        start = time.perf_counter()
        try:
            with _tracer.start_as_current_span("rerank", attributes={"candidates": len(hits), "top_n": limit}):
                ranked = reranker.rerank(query, [h["payload"].get("text", "") for h in hits], top_n=limit)
        except Exception as e:  # noqa: BLE001 - reranking is an enhancement
            logger.warning("Rerank failed (%s); falling back to dense order", e)
            return hits[:limit]

        out = [{**hits[r.index], "score": r.score, "dense_score": hits[r.index]["score"]} for r in ranked[:limit]]
        logger.debug(
            "Reranked %d candidates to %d in %.0fms",
            len(hits),
            len(out),
            (time.perf_counter() - start) * 1000,
        )
        # An empty result would silently answer from no context at all; the dense
        # order is a far better failure mode than none.
        return out or hits[:limit]

    def _expand_top_documents(
        self,
        tenant_id: str,
        hits: list[dict[str, Any]],
        *,
        access_keys: list[int] | None,
        tags: list[str] | None,
        folder_tags: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Add the best-ranked document's un-retrieved chunks after its top hit.

        Retrieval scores one passage at a time, so an answer spread across a
        document's sibling sections — a table of names, a spec list, the rest of a
        procedure — is invisible to top-k even when the document itself ranks
        first. Reading that whole document costs a fraction of the prompt and
        turns "I don't know" into an answer.

        Siblings are inserted directly after the hit that pulled them in, in
        ``chunk_order``, so the document reads contiguously and the surrounding
        ranked passages keep their relative order (citation numbers stay aligned
        because sources and context blocks both enumerate this one list). They
        carry ``expanded: True`` and score 0.0 — they were not vector matches.

        Never raises: expansion is an enhancement, so a failing scroll degrades
        to the plain ranked hits.
        """
        seen_ids = {hit.get("id") for hit in hits}
        expanded_docs: set[str] = set()
        out: list[dict[str, Any]] = []

        for hit in hits:
            out.append(hit)
            doc_key = str(hit.get("payload", {}).get("document_key") or "")
            if not doc_key or doc_key in expanded_docs or len(expanded_docs) >= _EXPAND_TOP_DOCS:
                continue
            expanded_docs.add(doc_key)
            try:
                siblings = self._stores.vector.list_document_chunks(
                    tenant_id=tenant_id,
                    document_key=doc_key,
                    limit=_EXPAND_MAX_CHUNKS,
                    access_keys=access_keys,
                    required_tags=tags,
                    any_tags=folder_tags,
                )
            except Exception as e:  # noqa: BLE001 - context enrichment must not fail a search
                logger.warning("Document expansion failed for %s: %s", doc_key, e)
                continue

            budget = _EXPAND_CHAR_BUDGET
            for sibling in siblings:
                if sibling.id in seen_ids:
                    continue
                text = sibling.payload.get("text", "")
                if len(text) > budget:
                    break
                budget -= len(text)
                seen_ids.add(sibling.id)
                out.append({"id": sibling.id, "score": 0.0, "payload": sibling.payload, "expanded": True})

        return out

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
        chunk_limit: int | None = None,
        expand_documents: bool = True,
    ) -> dict[str, Any]:
        """Hybrid RAG: vector retrieval + optional graph context → LLM synthesis.

        ``chunk_limit`` defaults to the configured ``CHAT_CHUNK_LIMIT``; an explicit
        value still wins, which is what makes A/B-ing the limit possible without a
        redeploy.
        """
        # 1. Vector retrieval
        vector_result = self.vector_search(
            tenant_id=tenant_id,
            query=query,
            limit=self._resolve_chunk_limit(chunk_limit),
            access_keys=access_keys,
            tags=tags,
            folder_tags=folder_tags,
            expand_documents=expand_documents,
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
        chunk_limit: int | None = None,
        expand_documents: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Streaming hybrid RAG chat.

        Yields event dicts with `type` in {"sources", "graph", "delta", "done", "error"}.
        The caller is responsible for serialising events to the wire format (e.g. SSE).

        ``chunk_limit`` defaults to the configured ``CHAT_CHUNK_LIMIT`` — see
        :meth:`vector_chat`. This is the path the UI chat actually takes.
        """
        # 1. Vector retrieval
        try:
            vector_result = self.vector_search(
                tenant_id=tenant_id,
                query=query,
                limit=self._resolve_chunk_limit(chunk_limit),
                access_keys=access_keys,
                tags=tags,
                folder_tags=folder_tags,
                expand_documents=expand_documents,
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
