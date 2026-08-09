"""Read back what compaction elided from this run's transcript.

The transcript an agent re-reads each turn is compacted (see
``services/agents/transcript.py``): an oversized tool result is carried as a preview
plus the call id, because paying for the whole thing on every subsequent turn is
where a long run's tokens actually go. The full result is never discarded — it is
persisted as an ``agent_run_step`` the moment the tool returns.

This is the way back to it. Without such a tool the compaction would be lossy in
practice even though it is lossless in storage: an agent that needed the detail
would have to re-run the tool, which costs the call again *and* re-elides.

Scoped to the run that is asking. An agent reading another run's steps is not a
capability anyone asked for, and taking the run id from the context rather than the
model's arguments makes it impossible rather than merely discouraged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from api.models.agent_run import AgentRunStep
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# One elided result can be large; this bounds what a single read can pull back into
# the context window, and the message says plainly when it bit.
_MAX_CHARS = 20_000


async def _read_run_detail(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    call_id = str(args.get("call_id") or "").strip()
    if not call_id:
        return {"error": "call_id is required"}
    if ctx.run_id is None:
        return {"error": "read_run_detail is only available inside an agent run"}

    rows = (
        (
            await ctx.session.execute(
                select(AgentRunStep)
                .where(
                    AgentRunStep.run_id == ctx.run_id,
                    AgentRunStep.org_id == ctx.org_id,
                    AgentRunStep.kind == "tool_result",
                )
                .order_by(AgentRunStep.seq)
            )
        )
        .scalars()
        .all()
    )
    step = next((s for s in rows if str((s.content or {}).get("call_id")) == call_id), None)
    if step is None:
        return {
            "error": f"No stored result for call_id '{call_id}' in this run.",
            "available": [str((s.content or {}).get("call_id")) for s in rows if (s.content or {}).get("call_id")][
                -20:
            ],
        }

    rendered = str((step.content or {}).get("result"))
    return {
        "call_id": call_id,
        "tool": step.name,
        "result": rendered[:_MAX_CHARS],
        "truncated": len(rendered) > _MAX_CHARS,
        "chars": len(rendered),
    }


READ_RUN_DETAIL = ToolSpec(
    name="read_run_detail",
    description=(
        "Read the full result of an earlier tool call in this run, when the transcript "
        "shows it was shortened. Pass the call_id given in the shortened result. Use "
        "this instead of re-running the tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "call_id": {
                "type": "string",
                "description": "The call_id from the shortened result's _detail block.",
            }
        },
        "required": ["call_id"],
    },
    category=Category.READ,
    handler=_read_run_detail,
    always_allowed=True,
)
