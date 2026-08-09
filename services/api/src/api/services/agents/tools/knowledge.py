"""Knowledge tools — RAG lookups scoped to the person the agent is acting for.

Always-allowed read tools (every agent gets them, still kind-gated). They reuse
the same brain-api client the workflow ``knowledge_search`` action uses.

**An agent reads with its actor's eyes, not the org's.** This used to pass only a
``tenant_id``, so every agent retrieved from the whole organisation's knowledge
base while every member-facing search passed the caller's permission mask. An
agent could therefore quote a document to someone who is not cleared to read it —
and because an agent's answer is prose, the disclosure carried no citation trail
that would make it obvious. The masks now come from ``ctx.actor_user_id``, so an
agent is exactly as capable as the person who set it running.

A run with **no** actor (a schedule, an inbound webhook) has no one to inherit
from. That case fails closed: the tool refuses unless the agent's grants opt in
with ``knowledge_scope: "org"``, which is a deliberate, auditable admin decision
rather than a silent default.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# grants.knowledge_scope
SCOPE_ACTOR = "actor"  # default: see what the run's actor can see
SCOPE_ORG = "org"  # opt-in: org-wide, for unattended runs with no actor


def _scope(ctx: ToolContext) -> str:
    return str((ctx.agent.grants or {}).get("knowledge_scope") or SCOPE_ACTOR)


_SEARCH_PARAMS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Natural-language question to answer from the KB."},
    },
    "required": ["query"],
}


async def _search_knowledge(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    if ctx.settings is None:
        return {"error": "knowledge search is not configured"}
    from api.services.brain_client import BrainAPIClient
    from api.services.org_llm import org_default_llm_model
    from api.services.search_access import resolve_profile_access_keys

    scope = _scope(ctx)
    if ctx.actor_user_id is not None:
        access_keys = await resolve_profile_access_keys(ctx.session, ctx.org_id, ctx.actor_user_id)
        if access_keys == []:
            # A profile with no membership in this org. Distinct from None
            # (unrestricted) and from a restricted mask list — there is nothing
            # this actor may read, so say so rather than silently searching wide.
            return {"error": "You have no knowledge-base access in this organization."}
    elif scope == SCOPE_ORG:
        access_keys = None  # unattended, explicitly granted org-wide reach
    else:
        return {
            "error": (
                "This run has no user to read on behalf of, so the knowledge base is "
                "unavailable. An admin can grant this agent org-wide knowledge access "
                'by setting grants.knowledge_scope to "org".'
            )
        }

    client = BrainAPIClient(ctx.settings)
    try:
        result = await client.vector_chat(
            tenant_id=str(ctx.org_id),
            query=query,
            access_keys=access_keys,
            # Org-pinned answer model (local vs 3rd-party); None = brain-api default.
            model=await org_default_llm_model(ctx.session, ctx.org_id),
        )
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, don't crash the run
        return {"error": f"knowledge search failed: {exc}"}
    answer = result.get("answer") or result.get("response") or result.get("result")
    sources = result.get("sources") or result.get("citations") or []
    # Trim source payloads so the tool result stays compact for the model.
    trimmed = [
        {k: s.get(k) for k in ("document_key", "title", "snippet", "section") if isinstance(s, dict) and k in s}
        for s in sources[:5]
        if isinstance(s, dict)
    ]
    return {"answer": answer, "sources": trimmed}


SEARCH_KNOWLEDGE = ToolSpec(
    name="search_knowledge",
    description="Answer a question using the organization's knowledge base (RAG).",
    parameters=_SEARCH_PARAMS,
    category=Category.READ,
    handler=_search_knowledge,
    always_allowed=True,
)
