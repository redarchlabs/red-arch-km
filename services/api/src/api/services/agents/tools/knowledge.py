"""Knowledge tools — org-scoped RAG lookups over the KM2 knowledge base.

Always-allowed read tools (every agent gets them, still kind-gated). They reuse
the same brain-api client the workflow ``knowledge_search`` action uses.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

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

    client = BrainAPIClient(ctx.settings)
    try:
        result = await client.vector_chat(tenant_id=str(ctx.org_id), query=query)
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
