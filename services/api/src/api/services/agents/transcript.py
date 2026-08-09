"""Keep an agent's transcript small without losing any of it.

A run re-sends its whole message list on every turn, so anything large in there is
paid for repeatedly — a 12k-character tool result on turn 2 of a twenty-turn run is
billed eighteen more times. That, not the model's own output, is where a long run's
tokens actually go.

The full record already exists: every tool call and result is persisted as an
``agent_run_step`` before it ever reaches the transcript. So nothing here needs to
*discard* anything. It only decides what the model re-reads:

* :func:`compact_tool_output` elides the oversized fields of one result, leaving the
  small ones intact plus a preview and the call id. The agent fetches the rest with
  ``read_run_detail`` on the rare turn it actually needs it.
* :func:`fold_old_turns` replaces the middle of a long transcript with one summary,
  keeping the system prompt, the original task, and the most recent turns verbatim.

Both are deterministic and side-effect-free; the summary text itself is produced by
the caller (see :func:`summarize_turns`), which is the only part that costs a model
call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The tool an agent calls to read back what was elided.
DETAIL_TOOL = "read_run_detail"

# Serialized characters of one tool result kept inline. Sized to hold an ordinary
# record or search hit whole, so the common case is never touched, while the
# outliers (file reads, CLI output, long list responses) fold.
DEFAULT_TOOL_RESULT_BUDGET = 2_000

# Serialized characters of the whole transcript before the middle is folded.
DEFAULT_TRANSCRIPT_BUDGET = 60_000

# Turns kept verbatim after a fold. The recent past is what the agent is actually
# reasoning about; the distant past is what the summary is for.
DEFAULT_KEEP_RECENT = 6

_PREVIEW_CHARS = 200


def transcript_chars(messages: list[dict[str, Any]]) -> int:
    """Serialized size of the transcript — the thing the budget is measured in."""
    return len(json.dumps(messages, default=str))


def _elision(value: Any, call_id: str) -> dict[str, Any]:
    rendered = value if isinstance(value, str) else json.dumps(value, default=str)
    return {
        "elided": True,
        "chars": len(rendered),
        "preview": rendered[:_PREVIEW_CHARS],
        "call_id": call_id,
    }


def compact_tool_output(
    output: Any,
    call_id: str,
    *,
    budget: int = DEFAULT_TOOL_RESULT_BUDGET,
) -> tuple[Any, bool]:
    """Return ``(payload, elided)`` — the result as the transcript should carry it.

    Fields are elided largest-first, so a result that is one huge blob beside three
    small flags keeps the flags: the model can still see whether the call succeeded
    without re-reading the blob. Stops as soon as the budget is met, and always
    leaves a pointer to the full record — a model that cannot see the way back will
    either guess or re-run the tool, and both cost more than the elision saved.
    """
    # A non-positive budget disables elision. Callers whose emitter does not persist
    # tool results use it: eliding what nothing recorded would be losing it, not
    # compacting it.
    if budget <= 0 or len(json.dumps(output, default=str)) <= budget:
        return output, False

    if not isinstance(output, dict):
        return {"result": _elision(output, call_id), "_detail": {"call_id": call_id, "tool": DETAIL_TOOL}}, True

    compacted = dict(output)
    compacted["_detail"] = {"call_id": call_id, "tool": DETAIL_TOOL}
    # Largest first: one pass of the biggest fields usually gets there, and eliding
    # a small field to save 40 characters costs the model more context than it saves.
    by_size = sorted(
        (k for k in output),
        key=lambda k: len(json.dumps(output[k], default=str)),
        reverse=True,
    )
    for key in by_size:
        if len(json.dumps(compacted, default=str)) <= budget:
            break
        compacted[key] = _elision(output[key], call_id)
    return _shrink_previews(compacted, budget), True


def _shrink_previews(compacted: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim the previews when eliding every field still leaves us over budget.

    Enough elided fields, each carrying 200 characters of preview, can exceed a
    tight budget on their own. The budget is a promise the caller relies on to
    bound a turn, so the previews give way rather than the promise.
    """
    previews = [key for key, value in compacted.items() if isinstance(value, dict) and value.get("elided")]
    for width in (100, 40, 0):
        if len(json.dumps(compacted, default=str)) <= budget:
            break
        for key in previews:
            compacted[key] = {**compacted[key], "preview": compacted[key]["preview"][:width]}
    return compacted


def _call_ids(message: dict[str, Any]) -> set[str]:
    return {str(call.get("id")) for call in message.get("tool_calls") or []}


def fold_old_turns(
    messages: list[dict[str, Any]],
    summary: str,
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> list[dict[str, Any]]:
    """Replace the middle of ``messages`` with one summary message.

    The head is preserved deliberately: the leading system messages carry the
    agent's instructions and the first user message carries the task. Folding those
    away would shrink the transcript by making the run forget what it is doing.

    The tail is trimmed forward past any tool message whose originating call is no
    longer present. A ``tool`` message whose ``tool_call_id`` refers to nothing is
    rejected outright by the provider, so a fold that split a call from its result
    would turn a cost saving into a failed run.
    """
    head: list[dict[str, Any]] = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        head.append(messages[index])
        index += 1
    if index < len(messages) and messages[index].get("role") == "user":
        head.append(messages[index])
        index += 1

    body = messages[index:]
    if len(body) <= keep_recent:
        return list(messages)

    split = max(0, len(body) - keep_recent) if keep_recent > 0 else len(body)
    # Widen the kept window backwards until no tool result is separated from the
    # assistant message that requested it. A ``tool`` message whose tool_call_id
    # refers to nothing is rejected outright by the provider, so the alternative —
    # dropping the orphan — would silently discard the result the agent just paid
    # for. Reaching back one message costs less than either.
    while split > 0:
        available = {cid for message in body[split:] for cid in _call_ids(message)}
        orphans = [m for m in body[split:] if m.get("role") == "tool" and str(m.get("tool_call_id")) not in available]
        if not orphans:
            break
        split -= 1
    recent = body[split:]

    folded = {
        "role": "system",
        "content": (
            f"Summary of {len(body) - len(recent)} earlier messages in this run "
            f"(full detail is retained; use {DETAIL_TOOL} to read any of it):\n{summary}"
        ),
    }
    return [*head, folded, *recent]


def _render_for_summary(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        content = message.get("content") or ""
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            lines.append(f"assistant called {function.get('name')}({function.get('arguments')})")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


_SUMMARY_INSTRUCTION = (
    "Summarize this portion of an AI agent's working transcript for the agent's own "
    "future reference. Preserve: what was asked, what was found or decided, what was "
    "changed, any identifiers (record ids, names, paths) it will need again, and "
    "anything still outstanding. Drop restatement, pleasantries, and tool output that "
    "was only read once. Write compact prose in the third person. No preamble."
)


async def summarize_turns(provider: Any, model: str, messages: list[dict[str, Any]]) -> str:
    """One cheap model call condensing ``messages`` into prose the agent can reuse.

    Failure is not fatal and must not be: the summary is an optimization, and a run
    that dies because its compaction call failed is strictly worse than a run that
    carries a slightly larger transcript. The caller keeps the uncompacted messages
    when this returns empty.
    """
    try:
        completion = await provider.complete(
            model=model,
            messages=[
                {"role": "system", "content": _SUMMARY_INSTRUCTION},
                {"role": "user", "content": _render_for_summary(messages)},
            ],
            reasoning_effort="minimal",
        )
    except Exception:  # noqa: BLE001 - an optimization must never fail the run
        logger.warning("transcript summarization failed; continuing uncompacted", exc_info=True)
        return ""
    return (completion.content or "").strip()
