"""Web research tool — a cited answer from the live web.

Two backends, chosen by which key the deployment has, because tying the only
live-web capability to one vendor's key meant an org without that key had no way to
read the web at all:

* **Anthropic** (preferred when an Anthropic key resolves) — the Messages API's
  server-side ``web_search`` **and** ``web_fetch`` tools. Both run on Anthropic's
  infrastructure; nothing executes here. ``web_fetch`` is the reason this is
  preferred: it opens a *specific URL* named in the question, which search-grounding
  cannot do — and "audit this page" is most of what anyone asks a researcher for.
* **Gemini** (fallback) — Google Search grounding on the AI Studio free tier
  (1,500 grounding requests/day). Gemini cannot mix Google Search with function
  tools in one request, so that path is a dedicated tool-less call.

Both return the same ``{"answer", "sources", "grounded"}`` shape — mirroring
``search_knowledge`` — so results flow through the runtime and console unchanged and
the agent never has to know which backend answered.

Governance: ``READ`` and ``side_effecting=False`` — it is a search, so it runs without
approval even under high-touch, the same posture as a read-only MCP search. Still
grant-gated: only agents with ``web_research`` in ``grants.tools`` are offered it.

It was ``EXECUTE`` at first, which the kind-gate reads as operator-only — so an
*advisory* agent could never call it, no matter its grants. That made a
research-analyst handed "audit this website" unable to fetch a single page: it asked
for permission it already had, then marked the whole order blocked. Reading the public
web is not acting on the world, and the category is the wrong lever for "this costs
money" — quota is handled inside the handler.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMError, LLMProvider
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# Google Search grounding tool payload (Gemini). Passed alone — never with function tools.
_GROUNDING_TOOL = [{"googleSearch": {}}]

# Anthropic's server-side research tools. The dated types are the dynamic-filtering
# variants: the model writes and runs code to filter results *before* they reach the
# context window. They need Opus 4.6+ / Sonnet 4.6+ — an older model configured in
# ``agent_web_search_model`` will be refused by the API rather than silently degraded.
_ANTHROPIC_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]

# Room for a researched answer with citations. Non-streaming, so kept under the
# SDK's HTTP-timeout guard rather than at the model's ceiling.
_MAX_TOKENS = 16000

# A server-tool turn that hits the API's own iteration cap comes back as
# ``pause_turn`` — resumable by sending the exchange straight back. Bounded so a
# genuinely unbounded search cannot loop on someone's bill.
_MAX_RESUMES = 4


def _is_quota_error(message: str) -> bool:
    low = message.lower()
    return "429" in message or "quota" in low or "exhaust" in low or "resource_exhausted" in low


def _sources_from(block: Any) -> list[dict[str, str]]:
    """Citations out of one Anthropic server-tool result block.

    On success ``content`` is a *list* of results; on failure it is a single error
    object (``{"error_code": ...}``) — same field, different shape, HTTP 200 either
    way. Iterating it blindly is how a quota error turns into a crash instead of a
    message, so the shape is checked rather than assumed.
    """
    content = getattr(block, "content", None)
    if not isinstance(content, list):
        return []
    out: list[dict[str, str]] = []
    for item in content:
        url = getattr(item, "url", None)
        if not url:
            continue
        out.append({"title": getattr(item, "title", None) or url, "url": url})
    return out


async def _anthropic_research(ctx: ToolContext, key: str, query: str) -> dict[str, Any]:
    from anthropic import AnthropicError, AsyncAnthropic

    client = AsyncAnthropic(api_key=key)
    messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
    answer: list[str] = []
    sources: list[dict[str, str]] = []
    errors: list[str] = []

    for _ in range(_MAX_RESUMES + 1):
        try:
            response = await client.messages.create(
                model=ctx.settings.agent_web_search_model,
                max_tokens=_MAX_TOKENS,
                tools=_ANTHROPIC_TOOLS,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )
        except AnthropicError as exc:
            if _is_quota_error(str(exc)):
                return {"error": "Anthropic rate limit reached for web research; try again shortly."}
            return {"error": f"web research failed: {exc}"}

        # Safety classifiers can decline with a normal 200 and an empty body. Read
        # stop_reason before content or this reads as an empty answer.
        if response.stop_reason == "refusal":
            return {"error": "The model declined this research request."}

        for block in response.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                answer.append(block.text)
            elif kind in ("web_search_tool_result", "web_fetch_tool_result"):
                found = _sources_from(block)
                sources.extend(found)
                if not found:
                    code = getattr(getattr(block, "content", None), "error_code", None)
                    if code:
                        errors.append(str(code))

        if response.stop_reason != "pause_turn":
            break
        # Resume by handing the paused turn straight back — no "continue" message;
        # the trailing server-tool block is what tells the API to pick up where it
        # stopped, and an extra user turn would derail it.
        messages = [{"role": "user", "content": query}, {"role": "assistant", "content": response.content}]

    text = "\n".join(part for part in answer if part.strip())
    if not text and errors:
        return {"error": f"web research failed: {', '.join(sorted(set(errors)))}"}
    # De-duplicated, order preserved: the same page cited twice is one source to a
    # reader, and the order the model found them in is the order it reasoned in.
    seen: set[str] = set()
    unique = [s for s in sources if not (s["url"] in seen or seen.add(s["url"]))]
    return {"answer": text, "sources": unique, "grounded": bool(unique)}


async def _gemini_research(ctx: ToolContext, key: str, query: str) -> dict[str, Any]:
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


async def _web_research(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    # Anthropic first: it can open a named URL, which grounding cannot.
    anthropic_key = await resolve_provider_key(ctx.session, ctx.org_id, "anthropic", ctx.settings)
    if anthropic_key:
        return await _anthropic_research(ctx, anthropic_key, query)

    gemini_key = await resolve_provider_key(ctx.session, ctx.org_id, "gemini", ctx.settings)
    if gemini_key:
        return await _gemini_research(ctx, gemini_key, query)

    # Name both ways out. A message naming only one key sends whoever reads it to
    # sign up for that vendor when they may already have the other.
    return {
        "error": (
            "Web research needs a key for one of its backends: set ANTHROPIC_API_KEY (search + "
            "page fetch) or GEMINI_API_KEY (search only), or add the org's anthropic/gemini key."
        )
    }


WEB_RESEARCH = ToolSpec(
    name="web_research",
    description=(
        "Research a question on the live web and return a concise, cited answer. Searches, and "
        "will open a specific page when the question names a URL — so it can be used to inspect a "
        "site, not just read about it. Use for current events, market/competitor facts, prices, "
        "anything on a public web page, or anything newer than your training data. Returns "
        "'answer' plus 'sources' (title + url). Read-only — no approval needed. Prefer "
        "search_knowledge for questions about this company's own documents."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "The research question or search query."}},
        "required": ["query"],
    },
    # READ, not EXECUTE: see the module docstring — an advisory researcher that
    # cannot read the web is not advisory, it is stuck. Grants still gate it.
    category=Category.READ,
    handler=_web_research,
    side_effecting=False,
)
