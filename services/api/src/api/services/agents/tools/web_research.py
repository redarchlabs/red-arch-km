"""Web research tool — grounded Google Search answers with citations.

Gives an agent a live-web research capability via **Gemini + Google Search grounding**
on the AI Studio free tier (1,500 grounding requests/day). Gemini cannot mix Google
Search with function tools in one request, so this is a dedicated **tool-less** call
(no KM2 function tools) that returns a concise answer plus its sources — mirroring the
``search_knowledge`` ``{"answer","sources"}`` shape so results flow through the runtime
and console unchanged.

Governance: ``EXECUTE`` (operator-only via the kind-gate) but ``side_effecting=False``
— it is read-only research, so it runs without approval even under high-touch, the same
posture as a read-only MCP search. Grant-gated: only agents with ``web_research`` in
``grants.tools`` are offered it.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMError, LLMProvider
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# Google Search grounding tool payload (Gemini). Passed alone — never with function tools.
_GROUNDING_TOOL = [{"googleSearch": {}}]


def _is_quota_error(message: str) -> bool:
    low = message.lower()
    return "429" in message or "quota" in low or "exhaust" in low or "resource_exhausted" in low


async def _web_research(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    key = await resolve_provider_key(ctx.session, ctx.org_id, "gemini", ctx.settings)
    if not key:
        return {"error": "Web research needs a Gemini API key (set GEMINI_API_KEY or the org's gemini key)."}

    provider = LLMProvider(api_key=key)
    try:
        result = await provider.complete(
            model=ctx.settings.agent_web_research_model,
            messages=[{"role": "user", "content": query}],
            tools=_GROUNDING_TOOL,
        )
    except LLMError as exc:
        if _is_quota_error(str(exc)):
            return {"error": "Daily free Google Search grounding quota (1,500/day) is exhausted; try again tomorrow."}
        return {"error": f"web research failed: {exc}"}

    return {
        "answer": result.content,
        "sources": [dict(s) for s in result.sources],
        "grounded": bool(result.sources),
    }


WEB_RESEARCH = ToolSpec(
    name="web_research",
    description=(
        "Research a question on the live web using Google Search and return a concise, cited "
        "answer. Use for current events, market/competitor facts, prices, or anything newer than "
        "your training data. Returns 'answer' plus 'sources' (title + url). Read-only — no approval "
        "needed. Prefer search_knowledge for questions about this company's own documents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The research question or search query."}
        },
        "required": ["query"],
    },
    category=Category.EXECUTE,
    handler=_web_research,
    side_effecting=False,
)
