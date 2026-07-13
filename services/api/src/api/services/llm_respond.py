"""Constrained LLM role-play + coaching for a training simulator (the ``llm_respond`` action).

The workflow is the controller — it owns the scenario, the objective, and the loop. This
module is the subordinate turn-taker: given a persona to play, optional grounding, the
conversation so far, and the learner's latest message, it returns ONE structured turn via a
strict JSON schema — the persona's in-character ``reply``, a brief ``coach`` tip for the
learner (a SEPARATE voice from the persona), and a ``done`` flag when the objective is met.
The strict schema guarantees a parseable, three-field result; on a malformed response we fall
back to empty strings + ``done=False`` so a driving workflow never crashes mid-scenario.

Kept tiny and side-effect-free (given a client) so it is easy to test and mock.
"""

from __future__ import annotations

import json
from typing import Any

# Default rules for the simulator. A workflow author overrides the behaviour per scenario
# via the node's ``persona``/``scenario``/``objective`` config, keeping the two voices
# (in-character persona vs. out-of-character coach) cleanly separated.
DEFAULT_SIMULATION_RULES = (
    "You run a training role-play. Play the given persona in first person, staying fully in "
    "character and consistent with the scenario; use ONLY the provided grounding for any facts "
    "and never invent policy. Never break character in 'reply'. Separately, as an out-of-character "
    "coach, give the LEARNER one or two short, constructive sentences on how to handle the "
    "situation better — address the learner, not the persona. Set 'done' to true only when the "
    "scenario's objective has been satisfactorily met or the conversation has reached a natural "
    "close. Return strictly the requested JSON."
)


def _respond_schema() -> dict[str, Any]:
    """A strict JSON schema for the persona reply + coach tip + done flag, so a simulator
    turn always yields a parseable three-field result."""
    return {
        "name": "simulator_turn",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "reply": {"type": "string", "description": "The persona's next in-character line."},
                "coach": {
                    "type": "string",
                    "description": "One or two short sentences of coaching for the learner (out of character).",
                },
                "done": {
                    "type": "boolean",
                    "description": "True when the scenario objective is met or the conversation has closed.",
                },
            },
            "required": ["reply", "coach", "done"],
        },
    }


def _format_history(history: list[dict[str, Any]]) -> str:
    lines = []
    for turn in history:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", ""))
        who = "Persona" if role in ("assistant", "persona", "bot") else "Learner"
        lines.append(f"{who}: {content}")
    return "\n".join(lines)


async def respond_action(
    client: Any,
    model: str,
    *,
    persona: str,
    user_message: str,
    scenario: str = "",
    objective: str = "",
    grounding: str = "",
    history: list[dict[str, Any]] | None = None,
    system: str | None = None,
) -> dict[str, Any]:
    """Return a structured ``{reply, coach, done}`` simulator turn.

    ``client`` is an ``AsyncOpenAI`` instance (typed ``Any`` to keep this import-light and
    mockable). ``scenario``/``objective``/``grounding``/``history`` are optional context. On a
    malformed model response, falls back to empty ``reply``/``coach`` + ``done=False`` so a
    driving workflow never crashes mid-scenario.
    """
    parts: list[str] = [f"Persona to play:\n{persona}"]
    if scenario:
        parts.append(f"Scenario:\n{scenario}")
    if objective:
        parts.append(f"Learning objective:\n{objective}")
    if grounding:
        parts.append(f"Grounding (use ONLY this for facts):\n{grounding}")
    if history:
        parts.append("Conversation so far:\n" + _format_history(history))
    parts.append(f"Learner said: {user_message}")
    parts.append(
        "Respond as JSON with the persona's in-character 'reply', a short out-of-character "
        "'coach' tip for the learner, and 'done'."
    )
    user = "\n\n".join(parts)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system or DEFAULT_SIMULATION_RULES},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_schema", "json_schema": _respond_schema()},
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "reply": str(parsed.get("reply") or ""),
        "coach": str(parsed.get("coach") or ""),
        "done": bool(parsed.get("done")),
    }
