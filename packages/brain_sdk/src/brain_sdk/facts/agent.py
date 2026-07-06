"""Agentic query engine — a JSON-action ReAct loop over the fact store.

Instead of a single vector query, a user question drives an iterative
tool-using loop: the LLM decomposes the question, calls tenant-scoped tools
(structured claim lookup, entity lookup, graph neighborhood, passage search),
observes results, refines, and finally answers with citations to the evidence
it gathered.

Design choices:

- **Provider-agnostic tool use.** The loop uses a strict JSON action protocol
  over :meth:`LLMClient.complete` (``json_object=True``) rather than any one
  provider's native tool-call format, so it runs identically on OpenAI
  (default), Claude, or Gemini.
- **Tenant isolation is server-side.** Every tool injects ``tenant_id`` and
  ``access_keys`` from the trusted :class:`AgentContext`; the model never
  supplies them.
- **Grounding.** Each tool observation is recorded as numbered evidence; the
  final answer must cite evidence ids, and the engine verifies every citation
  refers to evidence actually gathered (unsupported citations are flagged).
- **Bounded.** A hard iteration budget caps latency and cost.

The loop is exposed as :meth:`FactAgent.stream` (yields trace events, for SSE)
with :meth:`FactAgent.run` draining it into a single result.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from brain_sdk.facts.protocol import FactStore
from brain_sdk.llm.protocol import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

# A passage-search tool implementation: (query, limit, tenant_id, access_keys) -> hits.
VectorSearchFn = Callable[[str, int, str, "tuple[int, ...]"], list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Trusted, server-side scoping for a query. Never model-supplied."""

    tenant_id: str
    access_keys: tuple[int, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class AgentResult:
    answer: str
    citations: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    unsupported_citations: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = """\
You are a knowledge-base analyst. Answer the user's question ONLY from facts you \
retrieve with the tools below — never from prior knowledge. Work iteratively: \
decompose the question, gather evidence, then answer.

Respond with a SINGLE JSON object, nothing else. Either take an action:
  {"thought": "<reasoning>", "action": {"tool": "<name>", "args": { ... }}}
or give the final answer:
  {"thought": "<reasoning>", "final": {"answer": "<text with [E1] citations>", "citations": ["E1", ...]}}

Tools:
- claim_query {subject?, predicate?, object?, as_of?}: structured facts. BEST for \
specific facts, relationships, and "as of <date>" historical truth. as_of is an \
ISO date; omit for current truth.
- entity_lookup {name}: find an entity and everything known about it.
- neighborhood {name}: entities directly connected to a named entity.
- search_passages {query, limit?}: semantic search over the raw document text; \
use to ground or quote wording.
- corpus_overview {}: high-level summaries of the main entity clusters in the \
whole corpus. Use FIRST for broad or thematic questions ("what are the main \
themes", "what is this collection about").

Rules:
- Prefer claim_query/entity_lookup/neighborhood for facts; use search_passages for wording/quotes.
- The structured fact store is deliberately incomplete — many true facts live \
only in the raw document text. So if a fact tool (claim_query/entity_lookup/\
neighborhood) returns NO results, you MUST try search_passages before concluding \
anything: reformulate the question as a passage query and search the text. The \
document may state the answer even when no claim was extracted for it.
- Only answer "no information is available" AFTER search_passages has also come \
back empty. A fact-tool miss alone is never sufficient grounds to give up.
- Every observation is labelled [E<n>] evidence. Cite the evidence ids you used.
- Stop as soon as you can answer; do not loop needlessly.
"""


class FactAgent:
    """Runs the agentic query loop against the fact store (+ optional passages)."""

    def __init__(
        self,
        llm: LLMClient,
        fact_store: FactStore,
        *,
        vector_search: VectorSearchFn | None = None,
        max_iterations: int = 6,
    ) -> None:
        self._llm = llm
        self._store = fact_store
        self._vector_search = vector_search
        self._max_iterations = max_iterations

    # -- public ----------------------------------------------------------

    def run(self, question: str, ctx: AgentContext, *, history: list[LLMMessage] | None = None) -> AgentResult:
        result = AgentResult(answer="")
        for event in self.stream(question, ctx, history=history):
            result.trace.append(event)
            if event["type"] == "final":
                result.answer = event["answer"]
                result.citations = event["citations"]
                result.evidence = event["evidence"]
                result.iterations = event["iterations"]
                result.unsupported_citations = event["unsupported_citations"]
        return result

    def stream(
        self, question: str, ctx: AgentContext, *, history: list[LLMMessage] | None = None
    ) -> Iterator[dict[str, Any]]:
        messages: list[LLMMessage] = [LLMMessage("system", _SYSTEM_PROMPT)]
        messages.extend(history or [])
        messages.append(LLMMessage("user", f"Question: {question}"))

        evidence: list[dict[str, Any]] = []

        for iteration in range(1, self._max_iterations + 1):
            decision = self._think(messages)
            if decision is None:
                messages.append(LLMMessage("user", "Your last reply was not valid JSON. Reply with one JSON object."))
                yield {"type": "error", "message": "invalid JSON from model", "iteration": iteration}
                continue

            thought = str(decision.get("thought", ""))
            if thought:
                yield {"type": "thought", "content": thought, "iteration": iteration}

            if "final" in decision:
                yield self._finalize(decision["final"], evidence, iteration)
                return

            action = decision.get("action")
            if not isinstance(action, dict) or "tool" not in action:
                messages.append(LLMMessage("user", 'Reply must contain "action" or "final".'))
                continue

            tool = str(action["tool"])
            raw_args = action.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            yield {"type": "tool_call", "tool": tool, "args": args, "iteration": iteration}

            observation, records = self._exec_tool(tool, args, ctx)
            eid = f"E{len(evidence) + 1}"
            evidence.append({"id": eid, "tool": tool, "args": args, "result": records})
            yield {"type": "tool_result", "evidence_id": eid, "tool": tool, "records": records, "iteration": iteration}

            messages.append(LLMMessage("assistant", json.dumps(decision)))
            messages.append(LLMMessage("user", f"[{eid}] {tool} result:\n{observation}"))

        # Budget exhausted — force a best-effort answer from gathered evidence.
        yield self._forced_answer(messages, evidence)

    # -- loop internals --------------------------------------------------

    def _think(self, messages: list[LLMMessage]) -> dict[str, Any] | None:
        try:
            raw = self._llm.complete(messages, temperature=0.0, max_tokens=900, json_object=True)
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Agent decision parse failed: %s", exc)
            return None
        return parsed if isinstance(parsed, dict) else None

    def _finalize(self, final: Any, evidence: list[dict[str, Any]], iteration: int) -> dict[str, Any]:
        answer = ""
        citations: list[str] = []
        if isinstance(final, dict):
            answer = str(final.get("answer", ""))
            raw_citations = final.get("citations", [])
            if isinstance(raw_citations, list):
                citations = [str(c) for c in raw_citations]
        valid_ids = {e["id"] for e in evidence}
        unsupported = [c for c in citations if c not in valid_ids]
        return {
            "type": "final",
            "answer": answer,
            "citations": citations,
            "unsupported_citations": unsupported,
            "evidence": evidence,
            "iterations": iteration,
        }

    def _forced_answer(self, messages: list[LLMMessage], evidence: list[dict[str, Any]]) -> dict[str, Any]:
        messages.append(
            LLMMessage(
                "user",
                "You have reached the evidence-gathering limit. Answer now from the evidence "
                'gathered, as JSON: {"final": {"answer": "...", "citations": ["E1", ...]}}.',
            )
        )
        decision = self._think(messages) or {}
        final = decision.get("final", {"answer": "I could not gather enough evidence to answer confidently."})
        return self._finalize(final, evidence, self._max_iterations)

    # -- tools (all tenant-scoped server-side) ---------------------------

    def _exec_tool(self, tool: str, args: dict[str, Any], ctx: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        try:
            if tool == "claim_query":
                return self._tool_claim_query(args, ctx)
            if tool == "entity_lookup":
                return self._tool_entity_lookup(args, ctx)
            if tool == "neighborhood":
                return self._tool_neighborhood(args, ctx)
            if tool == "search_passages":
                return self._tool_search_passages(args, ctx)
            if tool == "corpus_overview":
                return self._tool_corpus_overview(ctx)
        except Exception as exc:  # noqa: BLE001 - a tool failure is an observation, not a crash
            logger.warning("Tool %s failed: %s", tool, exc)
            return f"tool error: {exc}", []
        return f"unknown tool: {tool}", []

    def _resolve_name(self, name: str, ctx: AgentContext) -> str | None:
        hits = self._store.find_entities(ctx.tenant_id, name=name, limit=1)
        return hits[0][0].entity_id if hits else None

    def _tool_claim_query(self, args: dict[str, Any], ctx: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        subject_id = None
        if args.get("subject"):
            subject_id = self._resolve_name(str(args["subject"]), ctx)
            if subject_id is None:
                return f"no entity found matching subject {args['subject']!r}", []
        rows = self._store.query_claims(
            ctx.tenant_id,
            subject_id=subject_id,
            predicate=str(args["predicate"]) if args.get("predicate") else None,
            object_value=str(args["object"]) if args.get("object") else None,
            as_of=str(args["as_of"]) if args.get("as_of") else None,
            access_keys=list(ctx.access_keys) or None,
            limit=int(args.get("limit", 50)),
        )
        return self._format_claims(rows), rows

    def _tool_entity_lookup(self, args: dict[str, Any], ctx: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        name = str(args.get("name", "")).strip()
        if not name:
            return "entity_lookup requires a name", []
        hits = self._store.find_entities(ctx.tenant_id, name=name, limit=3)
        if not hits:
            return f"no entity found matching {name!r}", []
        entity = hits[0][0]
        rows = self._store.query_claims(
            ctx.tenant_id, subject_id=entity.entity_id, access_keys=list(ctx.access_keys) or None, limit=50
        )
        summary = f"Entity: {entity.canonical_name} ({entity.type})\n" + self._format_claims(rows)
        return summary, rows

    def _tool_neighborhood(self, args: dict[str, Any], ctx: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        name = str(args.get("name", "")).strip()
        entity_id = self._resolve_name(name, ctx) if name else None
        if entity_id is None:
            return f"no entity found matching {name!r}", []
        rows = self._store.neighborhood(
            ctx.tenant_id, entity_id, access_keys=list(ctx.access_keys) or None, limit=int(args.get("limit", 50))
        )
        return self._format_claims(rows), rows

    def _tool_search_passages(self, args: dict[str, Any], ctx: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        if self._vector_search is None:
            return "passage search is not available", []
        query = str(args.get("query", "")).strip()
        if not query:
            return "search_passages requires a query", []
        limit = int(args.get("limit", 5))
        hits = self._vector_search(query, limit, ctx.tenant_id, ctx.access_keys)
        lines = [f"- {h.get('document_title', 'Untitled')}: {str(h.get('text', ''))[:400]}" for h in hits]
        return ("\n".join(lines) or "no passages found"), hits

    def _tool_corpus_overview(self, ctx: AgentContext) -> tuple[str, list[dict[str, Any]]]:
        communities = self._store.get_communities(ctx.tenant_id, limit=10)
        lines = [f"- {c.get('summary', '')}" for c in communities if c.get("summary")]
        return ("\n".join(lines) or "no corpus overview is available yet"), communities

    @staticmethod
    def _format_claims(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "(no matching facts)"
        out = []
        for r in rows:
            status = r.get("status")
            tag = f" [{status}]" if status and status != "active" else ""
            out.append(f"- {r.get('subject')} — {r.get('predicate')} — {r.get('object')}{tag}")
        return "\n".join(out)
