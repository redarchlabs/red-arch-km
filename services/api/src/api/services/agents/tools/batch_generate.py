"""Batch generation tools — single-shot text generation at the 50%-off async tier.

For latency-tolerant, no-tool generation (bulk drafting, digests, descriptions), the
Anthropic **Message Batches API** costs half the standard rate. This exposes it as two
tools: ``batch_generate`` submits a one-shot prompt and waits (bounded) for the result,
and ``check_batch`` retrieves a batch that was still processing when it returned.

Governance: ``EXECUTE`` (operator-only via the kind-gate), ``side_effecting=False`` — an
internal LLM generation, not an external egress, so it runs without approval. Grant-gated.

Uses the calling agent's own (Anthropic) model, so a Haiku operator batches on Haiku at
50% off. The bounded poll holds the run's DB session open while waiting, so ``max_wait``
is kept modest and batch is best used from scheduled/background runs; heavy production use
should move to a non-blocking submit + beat-driven retrieve.
"""

from __future__ import annotations

from typing import Any

from api.services.agents.llm.catalog import provider_for_model
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMError, LLMProvider
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec


async def _batch_generate(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}

    model = ctx.agent.model
    if provider_for_model(model) != "anthropic":
        return {"error": f"batch generation requires an Anthropic model; this agent uses '{model}'."}

    key = await resolve_provider_key(ctx.session, ctx.org_id, "anthropic", ctx.settings)
    if not key:
        return {"error": "batch generation needs an Anthropic API key."}

    system = str(args.get("system") or "").strip() or None
    try:
        max_tokens = int(args.get("max_tokens") or 1024)
    except (TypeError, ValueError):
        max_tokens = 1024

    provider = LLMProvider(api_key=key)
    try:
        return await provider.complete_batch(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            system=system,
            max_tokens=max_tokens,
            poll_interval=ctx.settings.agent_batch_poll_interval_seconds,
            max_wait=ctx.settings.agent_batch_max_wait_seconds,
        )
    except LLMError as exc:
        return {"error": f"batch generation failed: {exc}"}


async def _check_batch(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    batch_id = str(args.get("batch_id") or "").strip()
    if not batch_id:
        return {"error": "batch_id is required"}
    key = await resolve_provider_key(ctx.session, ctx.org_id, "anthropic", ctx.settings)
    if not key:
        return {"error": "batch retrieval needs an Anthropic API key."}
    provider = LLMProvider(api_key=key)
    try:
        return await provider.retrieve_batch(batch_id)
    except LLMError as exc:
        return {"error": f"batch retrieval failed: {exc}"}


BATCH_GENERATE = ToolSpec(
    name="batch_generate",
    description=(
        "Generate text at the 50%-off async batch tier — for latency-tolerant, single-shot "
        "generation (bulk drafts, digests, descriptions). Returns {status:'done', text} when it "
        "completes in time, or {status:'processing', batch_id} to retrieve later with check_batch. "
        "Not for interactive replies. Uses this agent's Anthropic model."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The generation prompt."},
            "system": {"type": "string", "description": "Optional system instruction."},
            "max_tokens": {"type": "integer", "description": "Max output tokens (default 1024)."},
        },
        "required": ["prompt"],
    },
    category=Category.EXECUTE,
    handler=_batch_generate,
    side_effecting=False,
)

CHECK_BATCH = ToolSpec(
    name="check_batch",
    description=(
        "Retrieve a batch_generate job by its batch_id: returns {status:'done', text} once ready, "
        "or {status:'processing'} if still running."
    ),
    parameters={
        "type": "object",
        "properties": {"batch_id": {"type": "string", "description": "The batch id from batch_generate."}},
        "required": ["batch_id"],
    },
    category=Category.EXECUTE,
    handler=_check_batch,
    side_effecting=False,
)
