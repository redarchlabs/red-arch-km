"""Constrained LLM 'steering' for a workflow-driven robot (the ``llm_decide`` action).

The workflow is the controller — it owns the goal, the loop, and the rules of engagement.
This module is the subordinate steering step: it turns a knowledge-base answer + the
visitor's utterance into ONE structured decision the robot can execute, CONSTRAINED to the
robot's action vocabulary (the gestures/moods a robot advertises at ``GET /capabilities``).
Because ``gesture`` and ``mood`` are enum-locked via a strict JSON schema, the model can only
ever choose actions the robot can actually perform — it steers within the workflow's rails
and never emits free-form behaviour.

Kept tiny and side-effect-free (given a client) so it is easy to test and mock.
"""

from __future__ import annotations

import json
from typing import Any

# Default rules of engagement. A workflow author overrides this per exhibit via the node's
# ``system`` config so the conversation is grounded by policy, not by an open-ended prompt.
DEFAULT_RULES = (
    "You are the mind of a friendly museum robot talking with children about space. "
    "Answer ONLY using the provided reference text; if it does not cover the question, "
    "say warmly in one short sentence that you are not familiar with that and steer back to "
    "space. Speak ONE short sentence, no markdown, no citation markers. Never break character "
    "by mentioning a knowledge base, documents, files, sources, or uploading — the child must "
    "never be reminded your answer comes from stored content. Stay kind, safe, and on-topic; "
    "gently decline anything off-topic or inappropriate."
)


def _decision_schema(gestures: list[str], moods: list[str]) -> dict[str, Any]:
    """A strict JSON schema whose gesture/mood are locked to the robot's vocabulary (plus
    null = 'no gesture/mood'), so the model cannot invent a move the robot can't perform."""
    return {
        "name": "robot_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "say": {"type": "string", "description": "One short sentence to speak aloud."},
                "gesture": {
                    "type": ["string", "null"],
                    "enum": [*gestures, None],
                    "description": "An optional gesture to perform from the allowed set, or null.",
                },
                "mood": {
                    "type": ["string", "null"],
                    "enum": [*moods, None],
                    "description": "An optional mood to show from the allowed set, or null.",
                },
                "done": {
                    "type": "boolean",
                    "description": "True when the exchange has reached a natural stopping point.",
                },
                "reason": {"type": "string", "description": "Brief rationale (not spoken)."},
            },
            "required": ["say", "gesture", "mood", "done", "reason"],
        },
    }


def _format_history(history: list[dict[str, Any]]) -> str:
    lines = []
    for turn in history:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", ""))
        who = "Robot" if role in ("assistant", "bot") else "Visitor"
        lines.append(f"{who}: {content}")
    return "\n".join(lines)


async def decide_action(
    client: Any,
    model: str,
    *,
    question: str,
    context: str = "",
    gestures: list[str] | None = None,
    moods: list[str] | None = None,
    system: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a structured ``{say, gesture, mood, done, reason}`` decision.

    ``client`` is an ``AsyncOpenAI`` instance (typed ``Any`` to keep this import-light and
    mockable). ``gesture``/``mood`` are constrained to ``gestures``/``moods`` (or null).
    Raises on a malformed model response — the caller records that on the step.
    """
    gestures = list(gestures or [])
    moods = list(moods or [])
    parts: list[str] = []
    if context:
        parts.append(f"Knowledge-base answer:\n{context}")
    if history:
        parts.append("Conversation so far:\n" + _format_history(history))
    parts.append(f"Visitor said: {question}")
    parts.append(
        "Decide the robot's next action as JSON. Choose 'gesture' and 'mood' ONLY from the "
        "allowed lists (or null for none)."
    )
    user = "\n\n".join(parts)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system or DEFAULT_RULES},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_schema", "json_schema": _decision_schema(gestures, moods)},
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)
