"""Constrained LLM authoring of ONE multiple-choice question (the ``llm_question`` action).

The workflow is the controller — it decides the topic, who the question is for, and what
to do with the result (store it as a quiz question, put it on a crew station, grade it
later). This module is the subordinate authoring step: it turns a topic + audience into
ONE question with four labelled options, the correct letter, and a hint, via a strict
JSON schema — so the caller always gets a complete, parseable, storable question instead
of prose it would have to parse.

Deliberately returns the SAME shape a stored question record uses (prompt + choice_a..d +
correct_choice + hint), so a create_record step can persist it field-for-field.

Kept tiny and side-effect-free (given a client) so it is easy to test and mock.
"""

from __future__ import annotations

import json
from typing import Any

from api.services.agents.llm.reasoning import reasoning_kwargs

CHOICE_LETTERS = ("A", "B", "C", "D")

DEFAULT_QUESTION_RULES = (
    "You write clear, factually correct multiple-choice questions. Follow these rules: "
    "write ONE question on the requested topic, pitched exactly at the requested audience's "
    "reading level and skill; give FOUR options where exactly one is unambiguously correct "
    "and the other three are plausible but clearly wrong; never write 'all of the above', "
    "'none of the above', or two options that could both be defended; vary which letter is "
    "correct; keep every option short. The hint must nudge toward the reasoning WITHOUT "
    "revealing the answer. Return strictly the requested JSON."
)


def _question_schema() -> dict[str, Any]:
    """A strict JSON schema bounding the model to one complete, storable question —
    four options and a correct letter, so the result is always usable as-is."""
    return {
        "name": "multiple_choice_question",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "description": "A short label for the question (3-5 words)."},
                "prompt": {"type": "string", "description": "The question itself, in one or two sentences."},
                "choice_a": {"type": "string", "description": "Option A."},
                "choice_b": {"type": "string", "description": "Option B."},
                "choice_c": {"type": "string", "description": "Option C."},
                "choice_d": {"type": "string", "description": "Option D."},
                "correct_choice": {
                    "type": "string",
                    "enum": list(CHOICE_LETTERS),
                    "description": "The letter of the one correct option.",
                },
                "hint": {
                    "type": "string",
                    "description": "One short nudge toward the reasoning; must not give the answer away.",
                },
            },
            "required": ["title", "prompt", "choice_a", "choice_b", "choice_c", "choice_d", "correct_choice", "hint"],
        },
    }


def _letter(value: Any) -> str:
    """Coerce the model's answer key to one of A-D. The strict schema should already
    bound it, but grading compares against this string, so a bad value must not become
    an unanswerable question — fall back to 'A'.

    Only a genuine letter choice is accepted: ``"a"``, ``"A."``, ``"(B)"`` all read as
    that letter, while a word like ``"banana"`` is garbage, NOT choice B — taking a first
    character would silently invent an answer key."""
    letters = [ch for ch in str(value or "").upper() if ch.isalpha()]
    return letters[0] if len(letters) == 1 and letters[0] in CHOICE_LETTERS else "A"


async def generate_question(
    client: Any,
    model: str,
    *,
    topic: str,
    audience: str = "",
    style: str = "",
    system: str | None = None,
) -> dict[str, Any]:
    """Return one structured multiple-choice question about ``topic``.

    ``client`` is an ``AsyncOpenAI`` instance (typed ``Any`` to keep this import-light and
    mockable). ``audience`` pitches the difficulty ("a 2nd grader", "a 9th-grade physics
    class"); ``style`` is optional extra framing ("in the voice of a station announcer").
    Raises on a malformed model response — the caller records that on the step.
    """
    parts = [f"Topic:\n{topic}"]
    if audience:
        parts.append(f"Write it for:\n{audience}")
    if style:
        parts.append(f"Style:\n{style}")
    parts.append("Return the question as JSON with title, prompt, choice_a..choice_d, correct_choice and hint.")

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system or DEFAULT_QUESTION_RULES},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
        response_format={"type": "json_schema", "json_schema": _question_schema()},
        **reasoning_kwargs(model),
    )
    parsed = json.loads(response.choices[0].message.content or "{}")
    return {
        "title": str(parsed.get("title") or "").strip(),
        "prompt": str(parsed.get("prompt") or "").strip(),
        "choice_a": str(parsed.get("choice_a") or "").strip(),
        "choice_b": str(parsed.get("choice_b") or "").strip(),
        "choice_c": str(parsed.get("choice_c") or "").strip(),
        "choice_d": str(parsed.get("choice_d") or "").strip(),
        "correct_choice": _letter(parsed.get("correct_choice")),
        "hint": str(parsed.get("hint") or "").strip(),
    }
